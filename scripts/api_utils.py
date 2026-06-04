"""Shared utilities for API-based ingestion scripts."""

from __future__ import annotations

from pathlib import Path
from datetime import date

import duckdb

DEFAULT_DB = "local_data/whenwin.duckdb"
DEFAULT_SCHEMA = "sql/schema.sql"


def connect(db_path: str = DEFAULT_DB, schema_path: str = DEFAULT_SCHEMA) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    con.execute(Path(schema_path).read_text(encoding="utf-8"))
    return con


def latest_result_date(con: duckdb.DuckDBPyConnection, league: str) -> date | None:
    """Return the most recent date with actual results for a league, or None."""
    row = con.execute(
        "SELECT MAX(date) FROM team_games WHERE league = ? AND result IS NOT NULL",
        [league],
    ).fetchone()
    return row[0] if row and row[0] else None


def resolve_team_id(
    con: duckdb.DuckDBPyConnection,
    league: str,
    season: int,
    city: str,
    team_name: str,
    cache: dict,
) -> str:
    """Look up our canonical team_id for a given league/season/city/name."""
    key = (league, season, city, team_name)
    if key in cache:
        return cache[key]

    row = con.execute(
        """
        SELECT team_id FROM teams
        WHERE league = ?
          AND city = ?
          AND team_name = ?
          AND start_year <= ?
          AND (end_year IS NULL OR end_year >= ?)
        ORDER BY start_year DESC
        LIMIT 1
        """,
        [league, city, team_name, season, season],
    ).fetchone()

    if not row:
        raise ValueError(
            f"Cannot resolve team_id: league={league} season={season} "
            f"city={city!r} team_name={team_name!r}"
        )
    cache[key] = row[0]
    return row[0]


def upsert_games(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    """Insert-or-replace team_game rows. Returns number of logical games inserted."""
    if not rows:
        return 0
    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id,
             result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows) // 2
