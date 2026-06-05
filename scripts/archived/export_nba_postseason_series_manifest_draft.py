#!/usr/bin/env python3
"""Draft an NBA postseason series manifest from existing `team_games` data.

Use this for in-progress seasons when you ingest games from basketball-reference
and need a series-level sheet to fill round_order/round_name.

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
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--gap-days", type=int, default=10)
    p.add_argument("--out", default="raw/nba_postseason_series_manifest_draft.csv")
    p.add_argument("--out-games", default="raw/nba_postseason_series_manifest_draft_games.csv")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)

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
            AND tg.date IS NOT NULL
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
        [args.from_season, args.to_season],
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

    by_matchup: dict[tuple[int, str, str], list[Game]] = {}
    for g in games:
        by_matchup.setdefault((g.season, g.team_a, g.team_b), []).append(g)

    series_rows: list[list[str]] = []
    game_rows: list[list[str]] = []

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
            series_id = f"nba_{season}_{team_a}_{team_b}_{idx}"
            series_rows.append(
                [
                    "NBA",
                    str(season),
                    series_id,
                    team_a,
                    seg_sorted[0].team_a_name,
                    team_b,
                    seg_sorted[0].team_b_name,
                    seg_sorted[0].game_date.isoformat(),
                    seg_sorted[-1].game_date.isoformat(),
                    str(len(seg_sorted)),
                    "",  # round_order
                    "",  # round_name
                ]
            )
            for g in seg_sorted:
                game_rows.append(
                    [
                        "NBA",
                        str(season),
                        g.game_id,
                        g.game_date.isoformat(),
                        series_id,
                        team_a,
                        g.team_a_name,
                        team_b,
                        g.team_b_name,
                    ]
                )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
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
        w.writerows(series_rows)

    out_games = Path(args.out_games)
    with out_games.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["league", "season", "game_id", "date", "series_id", "team_id_a", "team_a_name", "team_id_b", "team_b_name"])
        w.writerows(game_rows)

    print(f"series_rows: {len(series_rows)}")
    print(f"wrote: {out}")
    print(f"wrote: {out_games}")


if __name__ == "__main__":
    main()

