#!/usr/bin/env python3
"""Ingest NBA game results exported from basketball-reference.com schedule tables.

Expected CSV columns (as currently exported by basketball-reference):
  Date,Start (ET),Visitor/Neutral,PTS,Home/Neutral,PTS,... (other columns ignored)

This ingester:
- Derives `season` (start year) from the parsed date:
    month >= 9 => season = year, else season = year - 1
- Inserts two rows per real game into `team_games`
- Resolves `team_id` from the `teams` table by matching the full team name
  (e.g., "Oklahoma City Thunder") to "{city} {team_name}" with era containment by season.
- Marks `game_type` as configured via --game-type (default: regular).

Replace behavior:
- With --replace, deletes existing NBA rows for the chosen --game-type in the CSV's date range
  (inclusive) for the seasons encountered in the file, then inserts the file's games.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class TeamEra:
    key: str
    team_id: str
    start_year: int
    end_year: int | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--csv", default="raw/nba/basketball-reference/2026_04_14.csv")
    p.add_argument("--game-type", default="regular", choices=["regular", "postseason"])
    p.add_argument(
        "--replace",
        action="store_true",
        help="Delete NBA rows (for --game-type) in the CSV date range for seasons ingested",
    )
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


ALIASES: dict[str, str] = {
    # If your export ever uses short forms.
    "la clippers": "los angeles clippers",
    "la lakers": "los angeles lakers",
}


def apply_alias(full_name: str) -> str:
    v = " ".join(full_name.strip().split())
    v = ALIASES.get(v.lower(), v)
    return v


def parse_br_date(value: str) -> date:
    # Examples:
    # "Mon Mar 9 2026"
    # "Sun Apr 12 2026"
    v = " ".join(value.strip().split())
    return datetime.strptime(v, "%a %b %d %Y").date()


def season_from_date(d: date) -> int:
    return d.year if d.month >= 9 else d.year - 1


def load_nba_team_eras(con: duckdb.DuckDBPyConnection) -> dict[str, list[TeamEra]]:
    rows = con.execute(
        """
        select team_id, city, team_name, start_year, end_year
        from teams
        where league='NBA'
        """
    ).fetchall()
    out: dict[str, list[TeamEra]] = defaultdict(list)
    for team_id, city, team_name, start_year, end_year in rows:
        sy = int(start_year) if start_year is not None else 0
        ey = int(end_year) if end_year is not None else None
        full = f"{city} {team_name}".strip()
        key = norm_key(full)
        out[key].append(TeamEra(key=key, team_id=str(team_id), start_year=sy, end_year=ey))
    return out


def resolve_team_id(full_team_name: str, season: int, eras_index: dict[str, list[TeamEra]]) -> str:
    full = apply_alias(full_team_name)
    key = norm_key(full)
    eras = eras_index.get(key)
    if not eras:
        raise ValueError(f"No NBA team match for {full_team_name!r} (normalized={key})")
    matches = [e for e in eras if e.start_year <= season and (e.end_year is None or e.end_year >= season)]
    if not matches:
        raise ValueError(f"No NBA team era contains season={season} for {full_team_name!r}")
    # Prefer most recent identity for that season.
    return max(matches, key=lambda e: e.start_year).team_id


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    eras_index = load_nba_team_eras(con)

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")

    rows_to_insert: list[tuple] = []
    seasons_seen: set[int] = set()
    inserted_games = 0
    skipped = 0
    min_d: date | None = None
    max_d: date | None = None

    # Disambiguate any (very unlikely) same-day repeats.
    ordinals: dict[tuple[int, str, str, str], int] = {}

    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if not header:
            raise SystemExit("Empty CSV")

        for row in r:
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) < 6:
                skipped += 1
                continue

            raw_date = row[0].strip()
            visitor_name = row[2].strip()
            visitor_pts = row[3].strip()
            home_name = row[4].strip()
            home_pts = row[5].strip()

            if not raw_date or not visitor_name or not home_name:
                skipped += 1
                continue
            if not visitor_pts or not home_pts:
                skipped += 1
                continue

            d = parse_br_date(raw_date)
            season = season_from_date(d)
            seasons_seen.add(season)
            min_d = d if min_d is None or d < min_d else min_d
            max_d = d if max_d is None or d > max_d else max_d

            away_team_id = resolve_team_id(visitor_name, season, eras_index)
            home_team_id = resolve_team_id(home_name, season, eras_index)

            away_score = int(visitor_pts)
            home_score = int(home_pts)

            if home_score == away_score:
                home_res = away_res = "T"
            elif home_score > away_score:
                home_res, away_res = "W", "L"
            else:
                home_res, away_res = "L", "W"

            iso = d.isoformat()
            key = (season, iso, away_team_id, home_team_id)
            n = ordinals.get(key, 0) + 1
            ordinals[key] = n
            suffix = "" if n == 1 else f"_g{n}"
            game_id = f"nba_{season}_br_{iso}_{away_team_id}_at_{home_team_id}{suffix}"

            rows_to_insert.append(
                (game_id, iso, "NBA", season, home_team_id, away_team_id, home_res, home_score, away_score, args.game_type)
            )
            rows_to_insert.append(
                (game_id, iso, "NBA", season, away_team_id, home_team_id, away_res, away_score, home_score, args.game_type)
            )
            inserted_games += 1

    if args.replace and seasons_seen and min_d and max_d:
        con.execute(
            """
            DELETE FROM team_games
            WHERE league='NBA'
              AND game_type=?
              AND season IN (SELECT * FROM UNNEST(?))
              AND date BETWEEN ? AND ?
            """,
            [args.game_type, sorted(seasons_seen), min_d.isoformat(), max_d.isoformat()],
        )

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    print(f"Inserted {inserted_games} NBA games ({len(rows_to_insert)} team-game rows) from basketball-reference CSV")
    if seasons_seen and min_d and max_d:
        print(f"Seasons: {min(seasons_seen)}..{max(seasons_seen)} ({len(seasons_seen)} total), date_range={min_d}..{max_d}")
    if skipped:
        print(f"Skipped {skipped} rows (missing columns/scores)")


if __name__ == "__main__":
    main()
