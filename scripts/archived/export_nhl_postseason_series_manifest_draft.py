#!/usr/bin/env python3
"""Draft an NHL postseason series manifest from existing `team_games` data.

Goal:
- Produce a series-level CSV (one row per series) so you can manually fill in:
  - round_order
  - round_name
  - (optionally) adjust start/end dates if needed

Approach:
- Read distinct NHL postseason games from `team_games`
- Group by (season, unordered team pair)
- Split into multiple "series segments" if there is a long gap between games
  (to guard against data anomalies or rare repeated matchups)

This script does NOT modify the DB.
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--gap-days", type=int, default=7, help="Split a matchup into multiple series if gap between games exceeds this")
    p.add_argument("--out", default="raw/nhl_postseason_series_manifest_draft_1978plus.csv")
    p.add_argument("--ambiguous-out", default="raw/nhl_postseason_series_manifest_ambiguous.csv")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)

    rows = con.execute(
        """
        WITH g AS (
          SELECT DISTINCT
            season,
            game_id,
            date,
            CASE WHEN team_id < opponent_team_id THEN team_id ELSE opponent_team_id END AS team_a,
            CASE WHEN team_id < opponent_team_id THEN opponent_team_id ELSE team_id END AS team_b
          FROM team_games
          WHERE league='NHL'
            AND game_type='postseason'
            AND season BETWEEN ? AND ?
            AND date IS NOT NULL
        )
        SELECT season, game_id, date, team_a, team_b
        FROM g
        ORDER BY season, team_a, team_b, date, game_id
        """,
        [args.from_season, args.to_season],
    ).fetchall()

    games: list[Game] = []
    for season, game_id, d, team_a, team_b in rows:
        games.append(Game(int(season), str(game_id), d, str(team_a), str(team_b)))

    # group by matchup
    by_matchup: dict[tuple[int, str, str], list[Game]] = {}
    for g in games:
        by_matchup.setdefault((g.season, g.team_a, g.team_b), []).append(g)

    out_rows: list[tuple] = []
    ambiguous: list[tuple] = []

    for (season, team_a, team_b), glist in sorted(by_matchup.items()):
        glist_sorted = sorted(glist, key=lambda x: (x.game_date, x.game_id))

        segments: list[list[Game]] = []
        cur: list[Game] = []
        prev_date: date | None = None
        for g in glist_sorted:
            if prev_date is not None:
                gap = (g.game_date - prev_date).days
                if gap > args.gap_days:
                    if cur:
                        segments.append(cur)
                    cur = []
            cur.append(g)
            prev_date = g.game_date
        if cur:
            segments.append(cur)

        if len(segments) > 1:
            # Flag these for review; could be a real repeated matchup, or just off-days.
            ambiguous.append(
                (
                    season,
                    team_a,
                    team_b,
                    len(glist_sorted),
                    len(segments),
                    glist_sorted[0].game_date.isoformat(),
                    glist_sorted[-1].game_date.isoformat(),
                )
            )

        for idx, seg in enumerate(segments, start=1):
            seg_sorted = sorted(seg, key=lambda x: (x.game_date, x.game_id))
            start = seg_sorted[0].game_date.isoformat()
            end = seg_sorted[-1].game_date.isoformat()
            total_games = len(seg_sorted)
            series_id = f"nhl_{season}_{team_a}_{team_b}_{idx}"
            out_rows.append(
                (
                    "NHL",
                    season,
                    series_id,
                    team_a,
                    team_b,
                    start,
                    end,
                    total_games,
                    "",  # round_order
                    "",  # round_name
                )
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "league",
                "season",
                "series_id",
                "team_id_a",
                "team_id_b",
                "series_start_date",
                "series_end_date",
                "games_in_matchup",
                "round_order",
                "round_name",
            ]
        )
        for row in out_rows:
            w.writerow(list(row))

    amb_path = Path(args.ambiguous_out)
    with amb_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "season",
                "team_id_a",
                "team_id_b",
                "total_games_all_segments",
                "segments",
                "overall_start_date",
                "overall_end_date",
            ]
        )
        for row in ambiguous:
            w.writerow(list(row))

    print(f"series_rows: {len(out_rows)}")
    print(f"ambiguous_matchups: {len(ambiguous)} (gap_days>{args.gap_days})")
    print(f"wrote: {out_path}")
    print(f"wrote: {amb_path}")


if __name__ == "__main__":
    main()
