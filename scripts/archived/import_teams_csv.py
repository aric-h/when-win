#!/usr/bin/env python3
"""Import team identities from a CSV into teams/franchises (league-agnostic).

Expected columns:
- league (e.g. nfl, nba, nhl)
- location (city/region)
- team_name (mascot/name)
- from (start year)
- to (end year, blank/NULL/present allowed)
- franchise_id (root franchise key)

Team IDs are generated as <league>_<city-prefix>_<mascot>, using optional city-prefix overrides.
If multiple eras would collide on the same base ID, the current era (to is NULL) keeps the base ID and
other eras get a suffix: <base>_<fromYear>.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

from nfl_reference import load_city_prefix_overrides, team_id_for


@dataclass(frozen=True)
class TeamRow:
    league: str
    location: str
    team_name: str
    start_year: int
    end_year: int | None
    franchise_id: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--csv", default="raw/nfl/nfl_teams.csv")
    p.add_argument("--city-prefix-overrides", default="config/team_id_city_prefix_overrides.csv")
    return p.parse_args()


def clean_location(value: str) -> str:
    v = " ".join(value.strip().split())
    if v.lower() == "san fransisco":
        return "San Francisco"
    return v


def parse_end_year(value: str) -> int | None:
    v = value.strip()
    if not v:
        return None
    if v.lower() in {"null", "none", "present", "current"}:
        return None
    return int(v)


def read_rows(csv_path: Path) -> list[TeamRow]:
    rows: list[TeamRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"league", "location", "team_name", "from", "to", "franchise_id"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")

        for raw in reader:
            league = raw["league"].strip().upper()
            location = clean_location(raw["location"])
            team_name = " ".join(raw["team_name"].strip().split())
            start_year = int(raw["from"].strip())
            end_year = parse_end_year(raw["to"])
            franchise_id = raw["franchise_id"].strip()
            if not franchise_id:
                raise ValueError(f"Missing franchise_id for {league} {location} {team_name}")
            rows.append(TeamRow(league, location, team_name, start_year, end_year, franchise_id))
    return rows


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


def assign_team_ids(rows: list[TeamRow], city_prefix_overrides: dict[str, str]) -> dict[TeamRow, str]:
    grouped: dict[str, list[TeamRow]] = defaultdict(list)
    base_id: dict[TeamRow, str] = {}

    for r in rows:
        base = team_id_for(r.league, r.location, r.team_name, city_prefix_overrides)
        base_id[r] = base
        grouped[base].append(r)

    result: dict[TeamRow, str] = {}
    for base, group in grouped.items():
        if len(group) == 1:
            result[group[0]] = base
            continue

        current = [r for r in group if r.end_year is None]
        if current:
            keep = max(current, key=lambda r: r.start_year)
        else:
            keep = max(group, key=lambda r: (r.end_year or -1, r.start_year))

        result[keep] = base
        for r in group:
            if r is keep:
                continue
            result[r] = f"{base}_{r.start_year}"

    return result


def preferred_franchise_name(rows: list[TeamRow]) -> str:
    current = [r for r in rows if r.end_year is None]
    if current:
        r = max(current, key=lambda x: x.start_year)
        return f"{r.location} {r.team_name}".strip()
    r = max(rows, key=lambda x: (x.end_year or -1, x.start_year))
    return f"{r.location} {r.team_name}".strip()


def upsert(con: duckdb.DuckDBPyConnection, rows: list[TeamRow], team_ids: dict[TeamRow, str]) -> None:
    # Franchises: keep existing names if present, but ensure row exists.
    franchise_groups: dict[tuple[str, str], list[TeamRow]] = defaultdict(list)
    for r in rows:
        franchise_groups[(r.franchise_id, r.league)].append(r)

    for (franchise_id, league), group_rows in franchise_groups.items():
        start_year = min(r.start_year for r in group_rows)
        name = preferred_franchise_name(group_rows)
        existing = con.execute(
            "SELECT franchise_name, start_year FROM franchises WHERE franchise_id = ?",
            [franchise_id],
        ).fetchone()
        if existing:
            existing_name, existing_start = existing
            merged_start = existing_start if existing_start is not None else start_year
            if merged_start is not None:
                merged_start = min(merged_start, start_year)
            con.execute(
                "UPDATE franchises SET start_year = ? WHERE franchise_id = ?",
                [merged_start, franchise_id],
            )
            # Only overwrite placeholder names.
            if not existing_name or existing_name == franchise_id:
                con.execute(
                    "UPDATE franchises SET franchise_name = ?, league = ? WHERE franchise_id = ?",
                    [name, league, franchise_id],
                )
        else:
            con.execute(
                """
                INSERT INTO franchises (franchise_id, league, franchise_name, start_year)
                VALUES (?, ?, ?, ?)
                """,
                [franchise_id, league, name, start_year],
            )

    # Teams
    team_rows = []
    for r in rows:
        team_rows.append(
            (
                team_ids[r],
                r.league,
                r.location,
                r.team_name,
                r.start_year,
                r.end_year,
                r.franchise_id,
            )
        )

    con.executemany(
        """
        INSERT OR REPLACE INTO teams (team_id, league, city, team_name, start_year, end_year, franchise_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        team_rows,
    )


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    rows = read_rows(Path(args.csv))
    leagues = sorted({r.league for r in rows})
    overrides: dict[str, str] = {}
    for lg in leagues:
        overrides.update(load_city_prefix_overrides(args.city_prefix_overrides, league=lg))
    team_ids = assign_team_ids(rows, overrides)

    upsert(con, rows, team_ids)

    print(f"Imported {len(rows)} CSV rows")
    print(f"teams count: {con.execute('select count(*) from teams').fetchone()[0]}")
    print(f"franchises count: {con.execute('select count(*) from franchises').fetchone()[0]}")


if __name__ == "__main__":
    main()
