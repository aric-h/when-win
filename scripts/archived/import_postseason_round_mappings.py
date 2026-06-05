#!/usr/bin/env python3
"""Import postseason round mappings into DuckDB.

Populates:
- postseason_game_rounds

Inputs (defaults):
- MLB: raw/mlb_postseason_round_labels_1978plus.csv
- NBA: raw/nba_postseason_round_labels_1978plus_merged.csv
- NFL: raw/nfl_postseason_round_labels_1978plus.csv
- NHL: raw/nhl_postseason_round_labels_1978plus.csv

No changes are made to team_games; this is a separate mapping table.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb


MLB_ROUND_ORDER = {
    "Wild Card": 1,
    "Division Series (ALDS/NLDS)": 2,
    "League Championship Series (LCS)": 3,
    "World Series": 4,
}

NFL_ROUND_ORDER = {
    "Wild Card": 1,
    "Divisional": 2,
    "Conference Championship": 3,
    "Super Bowl": 4,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--reset", action="store_true", help="Delete existing rows before import")
    p.add_argument("--mlb", default="raw/mlb_postseason_round_labels_1978plus.csv")
    p.add_argument("--nba", default="raw/nba_postseason_round_labels_1978plus_merged.csv")
    p.add_argument("--nfl", default="raw/nfl_postseason_round_labels_1978plus.csv")
    p.add_argument("--nhl", default="raw/nhl_postseason_round_labels_1978plus.csv")
    p.add_argument(
        "--only-league",
        default=None,
        choices=[None, "MLB", "NBA", "NFL", "NHL"],
        help="Import only this league's mapping file (useful for incremental updates)",
    )
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS postseason_game_rounds (
            league TEXT NOT NULL,
            game_id TEXT NOT NULL,
            season INTEGER,
            round_order INTEGER,
            round_name TEXT NOT NULL,
            source TEXT,
            PRIMARY KEY (league, game_id)
        )
        """
    )


def load_mlb(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            gid = row["game_id"].strip()
            rn = row["round_name"].strip()
            ro = MLB_ROUND_ORDER.get(rn)
            season = int(gid.split("_", 2)[1]) if gid.startswith("mlb_") else None
            rows.append(("MLB", gid, season, ro, rn, "retrosheet_game_logs"))
    return rows


def load_nba(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            gid = row["game_id"].strip()
            season = int(row["season"])
            rn = row["round_name"].strip()
            ro = int(row["round_order"]) if row.get("round_order") and row["round_order"].strip() != "" else None
            src = (row.get("source") or "").strip() or "nba_1947_present"
            rows.append(("NBA", gid, season, ro, rn, src))
    return rows


def load_nfl(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            gid = row["game_id"].strip()
            season = int(row["season"])
            rn = row["round_name"].strip()
            ro = NFL_ROUND_ORDER.get(rn)
            src = "pfr_or_inferred"
            rows.append(("NFL", gid, season, ro, rn, src))
    return rows


def load_nhl(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            gid = row["game_id"].strip()
            season = int(row["season"])
            rn = row["round_name"].strip()
            ro = int(row["round_order"])
            rows.append(("NHL", gid, season, ro, rn, "series_manifest"))
    return rows


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    if args.reset:
        con.execute("DELETE FROM postseason_game_rounds")

    rows: list[tuple] = []
    if args.only_league in (None, "MLB"):
        rows.extend(load_mlb(Path(args.mlb)))
    if args.only_league in (None, "NBA"):
        rows.extend(load_nba(Path(args.nba)))
    if args.only_league in (None, "NFL"):
        rows.extend(load_nfl(Path(args.nfl)))
    if args.only_league in (None, "NHL"):
        rows.extend(load_nhl(Path(args.nhl)))

    con.executemany(
        """
        INSERT OR REPLACE INTO postseason_game_rounds
            (league, game_id, season, round_order, round_name, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    print("postseason_game_rounds:", con.execute("SELECT COUNT(*) FROM postseason_game_rounds").fetchone()[0])
    print(
        "by league:",
        con.execute("SELECT league, COUNT(*) FROM postseason_game_rounds GROUP BY league ORDER BY league").fetchall(),
    )


if __name__ == "__main__":
    main()
