#!/usr/bin/env python3
"""Ingest Kaggle NBA games CSV into DuckDB.

Input files:
- teams.csv: maps NBA TEAM_ID -> CITY/NICKNAME/ABBREVIATION
- games.csv: one row per game with home/visitor TEAM_IDs and points

This script:
- upserts NBA franchises and a default current identity per franchise into `franchises`/`teams`
- inserts two rows per game into `team_games`

Postseason detection:
- GAME_ID starting with '2' => regular
- GAME_ID starting with '4' => postseason
- other prefixes are skipped by default (preseason, play-in, etc.)
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import duckdb

from nfl_reference import load_city_prefix_overrides, team_id_for


@dataclass(frozen=True)
class TeamRef:
    team_api_id: int
    league: str
    city: str
    nickname: str
    abbreviation: str
    min_year: int | None
    year_founded: int | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--teams", default="raw/nba/kaggle/teams.csv")
    p.add_argument("--games", default="raw/nba/kaggle/nba_games_2004_2026.csv")
    p.add_argument("--city-prefix-overrides", default="config/team_id_city_prefix_overrides.csv")
    p.add_argument("--include-preseason", action="store_true")
    p.add_argument("--include-unknown-game-types", action="store_true")
    p.add_argument("--replace", action="store_true", help="Delete NBA rows for seasons present in input")
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


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def read_team_refs(path: Path) -> dict[int, TeamRef]:
    out: dict[int, TeamRef] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            team_api_id = int(row["TEAM_ID"].strip())
            out[team_api_id] = TeamRef(
                team_api_id=team_api_id,
                league="NBA",
                city=clean_text(row["CITY"]),
                nickname=clean_text(row["NICKNAME"]),
                abbreviation=clean_text(row["ABBREVIATION"]).upper(),
                min_year=int(row["MIN_YEAR"].strip()) if row.get("MIN_YEAR") else None,
                year_founded=int(row["YEARFOUNDED"].strip()) if row.get("YEARFOUNDED") else None,
            )
    return out


def franchise_id_for(team_api_id: int) -> str:
    return f"nba_franchise_{team_api_id}"


def team_id_for_ref(ref: TeamRef, city_prefix_overrides: dict[str, str]) -> str:
    return team_id_for(ref.league, ref.city, ref.nickname, city_prefix_overrides)


def upsert_franchises_and_teams(
    con: duckdb.DuckDBPyConnection,
    refs: dict[int, TeamRef],
    city_prefix_overrides: dict[str, str],
) -> dict[int, str]:
    team_id_by_api: dict[int, str] = {}

    franchise_rows = []
    team_rows = []

    for api_id, ref in refs.items():
        team_id = team_id_for_ref(ref, city_prefix_overrides)
        team_id_by_api[api_id] = team_id

        fid = franchise_id_for(api_id)
        franchise_name = f"{ref.city} {ref.nickname}".strip()
        franchise_start = ref.year_founded or ref.min_year
        franchise_rows.append((fid, "NBA", franchise_name, franchise_start))

        # Default identity: one per franchise. Historical identities can be added later with same franchise_id.
        team_start = ref.min_year or ref.year_founded
        team_rows.append((team_id, "NBA", ref.city, ref.nickname, team_start, None, fid))

    con.executemany(
        """
        INSERT OR REPLACE INTO franchises (franchise_id, league, franchise_name, start_year)
        VALUES (?, ?, ?, ?)
        """,
        franchise_rows,
    )

    con.executemany(
        """
        INSERT OR REPLACE INTO teams (team_id, league, city, team_name, start_year, end_year, franchise_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        team_rows,
    )

    return team_id_by_api


def classify_game_type(game_id: str) -> str | None:
    if not game_id:
        return None
    if game_id[0] == "2":
        return "regular"
    if game_id[0] == "4":
        return "postseason"
    if game_id[0] == "1":
        return "preseason"
    return None


def resolve_team_id(
    con: duckdb.DuckDBPyConnection,
    franchise_id: str,
    season: int,
    fallback_team_id: str,
) -> str:
    # Pick the most recent identity that is active for the season.
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
    return row[0] if row else fallback_team_id


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    city_overrides = load_city_prefix_overrides(args.city_prefix_overrides, league="NBA")
    refs = read_team_refs(Path(args.teams))
    fallback_team_id_by_api = upsert_franchises_and_teams(con, refs, city_overrides)

    # Determine seasons in file for optional replace.
    seasons_in_file: set[int] = set()
    with Path(args.games).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            seasons_in_file.add(int(row["SEASON"]))

    if args.replace and seasons_in_file:
        con.execute(
            "DELETE FROM team_games WHERE league = 'NBA' AND season IN (SELECT * FROM UNNEST(?))",
            [sorted(seasons_in_file)],
        )

    game_rows = []
    skipped = 0
    inserted_games = 0
    seen_game_ids: set[str] = set()

    with Path(args.games).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            season = int(row["SEASON"])
            game_id_raw = row["GAME_ID"].strip()
            game_type = classify_game_type(game_id_raw)

            if game_type == "preseason" and not args.include_preseason:
                skipped += 1
                continue
            if game_type is None and not args.include_unknown_game_types:
                skipped += 1
                continue
            if game_type not in {"regular", "postseason"}:
                # keep unknown/preseason out of team_games until you decide you want them
                skipped += 1
                continue
            if game_id_raw in seen_game_ids:
                # Source file can contain duplicate GAME_ID rows; keep the first occurrence.
                skipped += 1
                continue
            seen_game_ids.add(game_id_raw)

            game_id = f"nba_{game_id_raw}"
            date = row["GAME_DATE_EST"].strip()

            home_api_id = int(row["HOME_TEAM_ID"])
            away_api_id = int(row["VISITOR_TEAM_ID"])

            home_pts = int(float(row["PTS_home"]))
            away_pts = int(float(row["PTS_away"]))

            home_fid = franchise_id_for(home_api_id)
            away_fid = franchise_id_for(away_api_id)

            home_team_id = resolve_team_id(con, home_fid, season, fallback_team_id_by_api[home_api_id])
            away_team_id = resolve_team_id(con, away_fid, season, fallback_team_id_by_api[away_api_id])

            if home_pts == away_pts:
                home_res = away_res = "T"
            elif home_pts > away_pts:
                home_res, away_res = "W", "L"
            else:
                home_res, away_res = "L", "W"

            game_rows.append((game_id, date, "NBA", season, home_team_id, away_team_id, home_res, home_pts, away_pts, game_type))
            game_rows.append((game_id, date, "NBA", season, away_team_id, home_team_id, away_res, away_pts, home_pts, game_type))
            inserted_games += 1

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        game_rows,
    )

    print(f"Inserted {inserted_games} NBA games ({len(game_rows)} team-game rows)")
    if skipped:
        print(f"Skipped {skipped} rows (non-regular/non-postseason, or duplicates)")


if __name__ == "__main__":
    main()
