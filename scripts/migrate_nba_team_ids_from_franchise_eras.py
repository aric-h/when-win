#!/usr/bin/env python3
"""Re-resolve NBA team_ids in team_games based on teams-era rows.

After importing a comprehensive NBA teams CSV (multiple eras per franchise_id),
existing NBA `team_games` rows still reference older/current-only `team_id`s.

This script updates:
- team_games.team_id
- team_games.opponent_team_id

Resolution rule:
- Determine franchise_id from the current team_id via teams table.
- Pick the teams row for that franchise_id whose [start_year, end_year] contains `season`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--league", default="NBA")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    league = args.league.strip().upper()
    if league != "NBA":
        raise SystemExit("This migration script currently supports only league=NBA")

    con = duckdb.connect(str(Path(args.db)))

    con.execute("BEGIN TRANSACTION")
    try:
        # Update team_id
        con.execute(
            """
            UPDATE team_games tg
            SET team_id = (
                SELECT t_new.team_id
                FROM teams t_old
                JOIN teams t_new
                  ON t_new.league = 'NBA'
                 AND t_new.franchise_id = t_old.franchise_id
                WHERE t_old.league = 'NBA'
                  AND t_old.team_id = tg.team_id
                  AND t_new.start_year <= tg.season
                  AND (t_new.end_year IS NULL OR t_new.end_year >= tg.season)
                ORDER BY t_new.start_year DESC
                LIMIT 1
            )
            WHERE tg.league = 'NBA'
            """
        )

        # Update opponent_team_id
        con.execute(
            """
            UPDATE team_games tg
            SET opponent_team_id = (
                SELECT t_new.team_id
                FROM teams t_old
                JOIN teams t_new
                  ON t_new.league = 'NBA'
                 AND t_new.franchise_id = t_old.franchise_id
                WHERE t_old.league = 'NBA'
                  AND t_old.team_id = tg.opponent_team_id
                  AND t_new.start_year <= tg.season
                  AND (t_new.end_year IS NULL OR t_new.end_year >= tg.season)
                ORDER BY t_new.start_year DESC
                LIMIT 1
            )
            WHERE tg.league = 'NBA'
            """
        )

        # Validate: no NULLs introduced.
        nulls = con.execute(
            """
            SELECT
              sum(case when team_id is null then 1 else 0 end) as null_team,
              sum(case when opponent_team_id is null then 1 else 0 end) as null_opp
            FROM team_games
            WHERE league='NBA'
            """
        ).fetchone()
        if nulls and (nulls[0] or nulls[1]):
            raise RuntimeError(f"NULL team references after migration: team_id={nulls[0]} opponent_team_id={nulls[1]}")

        # Validate: still two rows per game_id
        bad = con.execute(
            """
            SELECT count(*)
            FROM (
              SELECT game_id
              FROM team_games
              WHERE league='NBA'
              GROUP BY game_id
              HAVING count(*) != 2
            )
            """
        ).fetchone()[0]
        if bad:
            raise RuntimeError(f"Found {bad} NBA game_ids with row_count != 2 after migration")

        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    print("NBA team_id/opponent_team_id migration complete")


if __name__ == "__main__":
    main()

