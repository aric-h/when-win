#!/usr/bin/env python3
"""Sanity checks for team_games integrity."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/whenwin.duckdb")
    parser.add_argument("--league", default="NFL")
    parser.add_argument("--season", type=int, default=2025)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))

    scope = [args.league, args.season]

    team_game_rows = con.execute(
        """
        SELECT COUNT(*)
        FROM team_games
        WHERE league = ? AND season = ?
        """,
        scope,
    ).fetchone()[0]

    distinct_games = con.execute(
        """
        SELECT COUNT(DISTINCT game_id)
        FROM team_games
        WHERE league = ? AND season = ?
        """,
        scope,
    ).fetchone()[0]

    bad_game_ids = con.execute(
        """
        SELECT COUNT(*)
        FROM (
          SELECT game_id, COUNT(*) AS c
          FROM team_games
          WHERE league = ? AND season = ?
          GROUP BY game_id
          HAVING c <> 2
        ) t
        """,
        scope,
    ).fetchone()[0]

    wins = con.execute(
        """
        SELECT COUNT(*)
        FROM team_games
        WHERE league = ? AND season = ? AND result = 'W'
        """,
        scope,
    ).fetchone()[0]

    losses = con.execute(
        """
        SELECT COUNT(*)
        FROM team_games
        WHERE league = ? AND season = ? AND result = 'L'
        """,
        scope,
    ).fetchone()[0]

    ties = con.execute(
        """
        SELECT COUNT(*)
        FROM team_games
        WHERE league = ? AND season = ? AND result = 'T'
        """,
        scope,
    ).fetchone()[0]

    expected_rows = distinct_games * 2

    print(f"League={args.league}, Season={args.season}")
    print(f"team_games rows           : {team_game_rows}")
    print(f"distinct game_id count    : {distinct_games}")
    print(f"expected rows (games*2)   : {expected_rows}")
    print(f"game_ids with row_count!=2: {bad_game_ids}")
    print(f"wins={wins}, losses={losses}, ties={ties}")

    ok = True
    if team_game_rows != expected_rows:
        print("FAIL: row count does not equal games*2")
        ok = False
    if bad_game_ids != 0:
        print("FAIL: some game_ids do not have exactly two rows")
        ok = False
    if wins != losses:
        print("FAIL: wins do not equal losses")
        ok = False

    if ok:
        print("PASS: all sanity checks passed")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
