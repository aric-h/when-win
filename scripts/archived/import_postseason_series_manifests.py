#!/usr/bin/env python3
"""Import postseason series manifests into DuckDB.

Populates:
- postseason_series

Inputs:
- NBA: raw/nba_postseason_series_manifest_draft_missing_rounds_filled.csv
- NHL: raw/nhl playoffs - nhl_postseason_series_manifest_draft_1978plus.csv

No changes are made to team_games.
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
    p.add_argument("--reset", action="store_true", help="Delete existing rows before import")
    p.add_argument("--nba", default="raw/nba_postseason_series_manifest_draft_missing_rounds_filled.csv")
    p.add_argument("--nhl", default="raw/nhl playoffs - nhl_postseason_series_manifest_draft_1978plus.csv")
    p.add_argument(
        "--only-league",
        default=None,
        choices=[None, "NBA", "NHL"],
        help="Import only this league's series manifest (useful for incremental updates)",
    )
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS postseason_series (
            league TEXT NOT NULL,
            season INTEGER NOT NULL,
            series_id TEXT NOT NULL,
            team_id_a TEXT,
            team_id_b TEXT,
            series_start_date DATE,
            series_end_date DATE,
            games_in_matchup INTEGER,
            round_order INTEGER,
            round_name TEXT,
            source TEXT,
            PRIMARY KEY (league, series_id)
        )
        """
    )


def load_generic(path: Path, league: str, source: str) -> list[tuple]:
    rows: list[tuple] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if (row.get("league") or "").strip().upper() not in {"", league}:
                continue
            rows.append(
                (
                    league,
                    int(row["season"]),
                    row["series_id"].strip(),
                    row["team_id_a"].strip(),
                    row["team_id_b"].strip(),
                    row["series_start_date"].strip(),
                    row["series_end_date"].strip(),
                    int(row["games_in_matchup"]),
                    int(row["round_order"]) if row.get("round_order") and row["round_order"].strip() != "" else None,
                    (row.get("round_name") or "").strip() or None,
                    source,
                )
            )
    return rows


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    if args.reset:
        con.execute("DELETE FROM postseason_series")

    rows: list[tuple] = []
    if args.only_league in (None, "NBA"):
        rows.extend(load_generic(Path(args.nba), "NBA", "nba_series_manifest_missing_rounds_filled"))
    if args.only_league in (None, "NHL"):
        rows.extend(load_generic(Path(args.nhl), "NHL", "nhl_series_manifest_1978plus_filled"))

    con.executemany(
        """
        INSERT OR REPLACE INTO postseason_series
            (league, season, series_id, team_id_a, team_id_b, series_start_date, series_end_date,
             games_in_matchup, round_order, round_name, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    print("postseason_series:", con.execute("SELECT COUNT(*) FROM postseason_series").fetchone()[0])
    print("by league:", con.execute("SELECT league, COUNT(*) FROM postseason_series GROUP BY league ORDER BY league").fetchall())


if __name__ == "__main__":
    main()
