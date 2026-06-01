#!/usr/bin/env python3
"""Import MLB team identities from raw/mlb/mlb_teams.csv.

This is MLB-specific because Retrosheet uses a 3-letter team code (e.g., NYA, SFN).
We build canonical team_id as:
  mlb_<retrosheet_code_lower>_<location_slug>_<team_name_slug>

The input CSV must include:
- league,location,team_name,from,to,franchise_id,retrosheet_code

Extra columns are ignored.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class Row:
    league: str
    location: str
    team_name: str
    start_year: int
    end_year: int | None
    franchise_id: str
    retrosheet_code: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--csv", default="raw/mlb/mlb_teams.csv")
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


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_end_year(value: str) -> int | None:
    v = value.strip()
    if not v or v.upper() == "NULL":
        return None
    return int(v)


def read_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        required = {"league", "location", "team_name", "from", "to", "franchise_id", "retrosheet_code"}
        missing = required - set(r.fieldnames or [])
        if missing:
            raise ValueError(f"Missing columns in {path}: {sorted(missing)}")

        for raw in r:
            league = raw["league"].strip().upper()
            if league != "MLB":
                # allow file to contain other leagues later, but only import MLB here
                continue
            location = " ".join(raw["location"].strip().split())
            team_name = " ".join(raw["team_name"].strip().split())
            start_year = int(raw["from"].strip())
            end_year = parse_end_year(raw["to"])
            franchise_id = raw["franchise_id"].strip()
            code = raw["retrosheet_code"].strip().upper()
            if not code:
                raise ValueError(f"Blank retrosheet_code for {location} {team_name}")
            rows.append(Row(league, location, team_name, start_year, end_year, franchise_id, code))
    return rows


def team_id_for(code: str, location: str, team_name: str) -> str:
    # Include location to prevent collisions when the same code+name recur in distinct eras
    # (e.g., ANA Anaheim Angels vs ANA Los Angeles Angels).
    return f"mlb_{code.lower()}_{norm(location)}_{norm(team_name)}"


def preferred_franchise_name(rows: list[Row]) -> str:
    current = [r for r in rows if r.end_year is None]
    if current:
        r = max(current, key=lambda x: x.start_year)
        return f"{r.location} {r.team_name}".strip()
    r = max(rows, key=lambda x: ((x.end_year or -1), x.start_year))
    return f"{r.location} {r.team_name}".strip()


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    rows = read_rows(Path(args.csv))
    if not rows:
        raise SystemExit("No MLB rows found to import")

    # Validate: no overlapping eras for same code.
    by_code: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_code[r.retrosheet_code].append(r)

    for code, eras in by_code.items():
        eras_sorted = sorted(eras, key=lambda x: x.start_year)
        prev_end = -1
        for e in eras_sorted:
            cur_end = 10**9 if e.end_year is None else e.end_year
            if e.start_year <= prev_end:
                raise ValueError(f"Overlapping eras for code {code}: start_year={e.start_year} overlaps prior end")
            prev_end = cur_end

    # Upsert franchises.
    by_franchise: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_franchise[r.franchise_id].append(r)

    for fid, group in by_franchise.items():
        start_year = min(r.start_year for r in group)
        name = preferred_franchise_name(group)
        existing = con.execute(
            "SELECT franchise_name, start_year FROM franchises WHERE franchise_id = ?",
            [fid],
        ).fetchone()
        if existing:
            existing_name, existing_start = existing
            merged_start = existing_start if existing_start is not None else start_year
            if merged_start is not None:
                merged_start = min(merged_start, start_year)
            con.execute("UPDATE franchises SET start_year = ? WHERE franchise_id = ?", [merged_start, fid])
            if not existing_name or existing_name == fid:
                con.execute("UPDATE franchises SET franchise_name = ?, league = 'MLB' WHERE franchise_id = ?", [name, fid])
        else:
            con.execute(
                "INSERT INTO franchises (franchise_id, league, franchise_name, start_year) VALUES (?, 'MLB', ?, ?)",
                [fid, name, start_year],
            )

    # Upsert teams.
    team_rows = []
    for r in rows:
        team_rows.append(
            (
                team_id_for(r.retrosheet_code, r.location, r.team_name),
                "MLB",
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

    print(f"Imported {len(rows)} MLB identity rows")
    print("MLB franchises:", con.execute("select count(*) from franchises where league='MLB'").fetchone()[0])
    print("MLB teams:", con.execute("select count(*) from teams where league='MLB'").fetchone()[0])


if __name__ == "__main__":
    main()
