#!/usr/bin/env python3
"""Gap-fill NBA games from nba_1947_present.csv into team_games.

Input: raw/nba/kaggle/nba_1947_present.csv

Key behaviors:
- Derives `season` (start year) from gameDateTimeEst (month>=9 => same year else year-1)
- Inserts two rows per game into `team_games`
- Resolves canonical `team_id` from `teams` via `franchise_id = nba_franchise_<teamApiId>`
- Maps gameType into your schema:
  - Regular Season / NBA Cup / Emirates Cup => regular
  - Playoffs / Play-in Tournament => postseason
  - Preseason / All-Star Game => skipped

This is intended to backfill the gap after the Kaggle 2004-2022 dataset.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--csv", default="raw/nba/kaggle/nba_1947_present.csv")
    p.add_argument("--min-season", type=int, default=2023)
    p.add_argument("--max-season", type=int)
    p.add_argument("--replace", action="store_true", help="Delete NBA rows for seasons ingested")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
    con.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS franchise_id TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS franchises (
            franchise_id TEXT PRIMARY KEY,
            league TEXT NOT NULL,
            franchise_name TEXT NOT NULL,
            start_year INTEGER
        )
        """
    )


def season_from_dt(dt: datetime) -> int:
    return dt.year if dt.month >= 9 else dt.year - 1


def franchise_id_for(team_api_id: int) -> str:
    return f"nba_franchise_{team_api_id}"


def resolve_team_id(
    con: duckdb.DuckDBPyConnection,
    franchise_id: str,
    season: int,
    cache: dict[tuple[str, int], str],
) -> str:
    key = (franchise_id, season)
    if key in cache:
        return cache[key]

    row = con.execute(
        """
        SELECT team_id
        FROM teams
        WHERE league = 'NBA'
          AND franchise_id = ?
          AND start_year <= ?
          AND (end_year IS NULL OR end_year >= ?)
        ORDER BY start_year DESC
        LIMIT 1
        """,
        [franchise_id, season, season],
    ).fetchone()

    if not row:
        raise ValueError(f"No NBA team_id found for franchise_id={franchise_id} season={season}")

    cache[key] = row[0]
    return row[0]


def map_game_type(game_type: str) -> str | None:
    gt = game_type.strip()
    if gt in {"Regular Season", "NBA Emirates Cup", "NBA Cup", "Emirates NBA Cup", "Emirates Cup"}:
        return "regular"
    if gt in {"Playoffs", "Play-in Tournament"}:
        return "postseason"
    if gt in {"Preseason", "All-Star Game"}:
        return None
    return None


def main() -> None:
    args = parse_args()

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    rows_to_insert: list[tuple] = []
    seasons_seen: set[int] = set()
    cache: dict[tuple[str, int], str] = {}

    inserted_games = 0
    skipped = 0

    with Path(args.csv).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            dt = datetime.strptime(row["gameDateTimeEst"].strip(), "%Y-%m-%d %H:%M:%S")
            season = season_from_dt(dt)
            if season < args.min_season:
                continue
            if args.max_season is not None and season > args.max_season:
                continue

            game_type = map_game_type(row.get("gameType", ""))
            if game_type is None:
                skipped += 1
                continue

            # Skip unplayed rows (if any).
            hs = row.get("homeScore", "").strip()
            aw = row.get("awayScore", "").strip()
            if not hs or not aw:
                skipped += 1
                continue

            home_score = int(float(hs))
            away_score = int(float(aw))
            if home_score == 0 and away_score == 0:
                skipped += 1
                continue

            home_api_id = int(row["hometeamId"].strip())
            away_api_id = int(row["awayteamId"].strip())

            home_team_id = resolve_team_id(con, franchise_id_for(home_api_id), season, cache)
            away_team_id = resolve_team_id(con, franchise_id_for(away_api_id), season, cache)

            if home_score == away_score:
                home_res = away_res = "T"
            elif home_score > away_score:
                home_res, away_res = "W", "L"
            else:
                home_res, away_res = "L", "W"

            game_id_raw = row["gameId"].strip()
            game_id = f"nba_{game_id_raw}"
            date = dt.date().isoformat()

            rows_to_insert.append((game_id, date, "NBA", season, home_team_id, away_team_id, home_res, home_score, away_score, game_type))
            rows_to_insert.append((game_id, date, "NBA", season, away_team_id, home_team_id, away_res, away_score, home_score, game_type))

            inserted_games += 1
            seasons_seen.add(season)

    if args.replace and seasons_seen:
        con.execute(
            "DELETE FROM team_games WHERE league = 'NBA' AND season IN (SELECT * FROM UNNEST(?))",
            [sorted(seasons_seen)],
        )

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    print(f"Inserted {inserted_games} NBA games ({len(rows_to_insert)} team-game rows)")
    if seasons_seen:
        print(f"Seasons: {min(seasons_seen)}..{max(seasons_seen)} ({len(seasons_seen)} total)")
    if skipped:
        print(f"Skipped {skipped} rows (non-counting game types or missing scores)")


if __name__ == "__main__":
    main()
