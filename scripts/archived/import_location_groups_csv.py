#!/usr/bin/env python3
"""Import location groupings from raw/location_groups.csv into DuckDB.

Expected CSV columns (current export from teams + location_group):
- team_id
- location_group

Other columns are ignored.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--csv", default="raw/location_groups.csv")
    p.add_argument("--reset", action="store_true", help="Delete existing location group mappings before import")
    p.add_argument("--cleanup", action="store_true", help="Remove known-bad duplicate team rows when safe")
    return p.parse_args()


def titleize_group_id(group_id: str) -> str:
    return " ".join(part.capitalize() for part in group_id.strip().split("_") if part)


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS location_groups (
            location_group_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS team_location_groups (
            team_id TEXT PRIMARY KEY,
            location_group_id TEXT NOT NULL
        )
        """
    )


def cleanup_duplicates(con: duckdb.DuckDBPyConnection) -> None:
    dup_team_id = "mlb_ath_n_a_athletics"
    referenced = con.execute(
        "SELECT COUNT(*) FROM team_games WHERE team_id = ?",
        [dup_team_id],
    ).fetchone()[0]
    if referenced:
        raise RuntimeError(f"Refusing to delete {dup_team_id}: referenced by {referenced} team_games rows")
    con.execute("DELETE FROM teams WHERE team_id = ?", [dup_team_id])


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    schema_path = Path(args.schema)
    csv_path = Path(args.csv)

    con = duckdb.connect(str(db_path))
    ensure_schema(con, schema_path)

    if args.cleanup:
        cleanup_duplicates(con)

    if args.reset:
        con.execute("DELETE FROM team_location_groups")
        con.execute("DELETE FROM location_groups")

    groups: dict[str, str] = {}
    mappings: list[tuple[str, str]] = []

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team_id = (row.get("team_id") or "").strip()
            group_id = (row.get("location_group") or "").strip()
            if not team_id or not group_id:
                continue
            groups.setdefault(group_id, titleize_group_id(group_id))
            mappings.append((team_id, group_id))

    for group_id, name in sorted(groups.items()):
        con.execute(
            "INSERT OR IGNORE INTO location_groups (location_group_id, name) VALUES (?, ?)",
            [group_id, name],
        )

    missing_teams = 0
    for team_id, group_id in mappings:
        exists = con.execute("SELECT 1 FROM teams WHERE team_id = ? LIMIT 1", [team_id]).fetchone()
        if not exists:
            missing_teams += 1
            continue
        con.execute(
            "INSERT OR REPLACE INTO team_location_groups (team_id, location_group_id) VALUES (?, ?)",
            [team_id, group_id],
        )

    if missing_teams:
        print(f"warning: skipped {missing_teams} team_id values not present in teams table")

    print("location_groups:", con.execute("SELECT COUNT(*) FROM location_groups").fetchone()[0])
    print("team_location_groups:", con.execute("SELECT COUNT(*) FROM team_location_groups").fetchone()[0])


if __name__ == "__main__":
    main()

