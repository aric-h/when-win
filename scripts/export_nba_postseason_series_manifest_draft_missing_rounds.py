#!/usr/bin/env python3
"""Draft an NBA postseason *series* manifest for games missing round labels.

This is a helper for manually filling in round_name/round_order at the series level
instead of per-game.

Inputs:
- Existing per-game mapping CSV (produced earlier):
  raw/nba_postseason_round_labels_1978plus.csv

DB:
- team_games (NBA postseason games)

Outputs:
- raw/nba_postseason_series_manifest_draft_missing_rounds.csv
  One row per inferred series segment, with empty round fields.
- raw/nba_postseason_series_manifest_draft_missing_rounds_games.csv
  One row per game, mapping game_id -> series_id, with date and team names for context.

No DB modifications.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class Game:
    season: int
    game_id: str
    game_date: date
    team_a: str
    team_b: str
    team_a_name: str
    team_b_name: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--mapping", default="raw/nba_postseason_round_labels_1978plus.csv")
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--gap-days", type=int, default=10, help="Split a matchup into multiple series if gap exceeds this")
    p.add_argument("--out-series", default="raw/nba_postseason_series_manifest_draft_missing_rounds.csv")
    p.add_argument("--out-games", default="raw/nba_postseason_series_manifest_draft_missing_rounds_games.csv")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)

    # Pull the set of NBA postseason game_ids that are missing round labels.
    missing_rows = con.execute(
        """
        SELECT game_id
        FROM read_csv_auto(?, header=true)
        WHERE (round_name IS NULL OR round_name = '')
          AND season BETWEEN ? AND ?
        """,
        [args.mapping, args.from_season, args.to_season],
    ).fetchall()
    missing_game_ids = {str(r[0]) for r in missing_rows}

    if not missing_game_ids:
        print("No missing round_name rows found in mapping; nothing to export.")
        return

    # Pull distinct games from DB for those missing IDs.
    # We canonicalize the matchup as (team_a, team_b) unordered.
    rows = con.execute(
        """
        WITH g AS (
          SELECT DISTINCT
            tg.season,
            tg.game_id,
            tg.date,
            CASE WHEN tg.team_id < tg.opponent_team_id THEN tg.team_id ELSE tg.opponent_team_id END AS team_a,
            CASE WHEN tg.team_id < tg.opponent_team_id THEN tg.opponent_team_id ELSE tg.team_id END AS team_b
          FROM team_games tg
          WHERE tg.league='NBA'
            AND tg.game_type='postseason'
            AND tg.season BETWEEN ? AND ?
            AND tg.game_id IN (SELECT * FROM UNNEST(?))
        )
        SELECT
          g.season,
          g.game_id,
          g.date,
          g.team_a,
          ta.team_name AS team_a_name,
          g.team_b,
          tb.team_name AS team_b_name
        FROM g
        JOIN teams ta ON ta.team_id = g.team_a
        JOIN teams tb ON tb.team_id = g.team_b
        ORDER BY g.season, g.team_a, g.team_b, g.date, g.game_id
        """,
        [args.from_season, args.to_season, sorted(missing_game_ids)],
    ).fetchall()

    games: list[Game] = []
    for season, game_id, d, team_a, team_a_name, team_b, team_b_name in rows:
        games.append(
            Game(
                season=int(season),
                game_id=str(game_id),
                game_date=d,
                team_a=str(team_a),
                team_b=str(team_b),
                team_a_name=str(team_a_name),
                team_b_name=str(team_b_name),
            )
        )

    # Group by matchup pair
    by_matchup: dict[tuple[int, str, str], list[Game]] = {}
    for g in games:
        by_matchup.setdefault((g.season, g.team_a, g.team_b), []).append(g)

    series_rows: list[tuple] = []
    game_rows: list[tuple] = []

    for (season, team_a, team_b), glist in sorted(by_matchup.items()):
        glist_sorted = sorted(glist, key=lambda x: (x.game_date, x.game_id))

        segments: list[list[Game]] = []
        cur: list[Game] = []
        prev: date | None = None
        for g in glist_sorted:
            if prev is not None and (g.game_date - prev).days > args.gap_days:
                if cur:
                    segments.append(cur)
                cur = []
            cur.append(g)
            prev = g.game_date
        if cur:
            segments.append(cur)

        for idx, seg in enumerate(segments, start=1):
            seg_sorted = sorted(seg, key=lambda x: (x.game_date, x.game_id))
            start = seg_sorted[0].game_date.isoformat()
            end = seg_sorted[-1].game_date.isoformat()
            games_in_matchup = len(seg_sorted)
            team_a_name = seg_sorted[0].team_a_name
            team_b_name = seg_sorted[0].team_b_name
            series_id = f"nba_{season}_{team_a}_{team_b}_{idx}"

            series_rows.append(
                (
                    "NBA",
                    season,
                    series_id,
                    team_a,
                    team_a_name,
                    team_b,
                    team_b_name,
                    start,
                    end,
                    games_in_matchup,
                    "",  # round_order
                    "",  # round_name
                )
            )

            for g in seg_sorted:
                game_rows.append(
                    (
                        "NBA",
                        season,
                        g.game_id,
                        g.game_date.isoformat(),
                        series_id,
                        team_a,
                        team_a_name,
                        team_b,
                        team_b_name,
                    )
                )

    out_series = Path(args.out_series)
    out_series.parent.mkdir(parents=True, exist_ok=True)
    with out_series.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "league",
                "season",
                "series_id",
                "team_id_a",
                "team_a_name",
                "team_id_b",
                "team_b_name",
                "series_start_date",
                "series_end_date",
                "games_in_matchup",
                "round_order",
                "round_name",
            ]
        )
        for row in series_rows:
            w.writerow(list(row))

    out_games = Path(args.out_games)
    with out_games.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "league",
                "season",
                "game_id",
                "date",
                "series_id",
                "team_id_a",
                "team_a_name",
                "team_id_b",
                "team_b_name",
            ]
        )
        for row in game_rows:
            w.writerow(list(row))

    print(f"missing_game_ids: {len(missing_game_ids)}")
    print(f"series_rows: {len(series_rows)}")
    print(f"wrote: {out_series}")
    print(f"wrote: {out_games}")


if __name__ == "__main__":
    main()

