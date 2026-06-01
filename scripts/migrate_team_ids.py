#!/usr/bin/env python3
"""Migrate team_id format and backfill franchise metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from nfl_reference import NAME_TO_REF, TEAM_REFERENCE, load_city_prefix_overrides, team_id_for


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/whenwin.duckdb")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--city-prefix-overrides", default="config/team_id_city_prefix_overrides.csv")
    return parser.parse_args()


def ensure_schema_bits(con: duckdb.DuckDBPyConnection) -> None:
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


def fetch_team_rows(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        """
        SELECT team_id, league, city, team_name, start_year, end_year, franchise_id
        FROM teams
        ORDER BY team_id
        """
    ).fetchall()


def merge_nullable(existing, incoming):
    return existing if existing is not None else incoming


def merge_start_year(existing, incoming):
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    return min(existing, incoming)


def franchise_for(league: str, city: str, team_name: str) -> tuple[str, int | None, str]:
    if league.upper() == "NFL":
        key = f"{city} {team_name}"
        ref = NAME_TO_REF.get(key)
        if ref:
            return ref["franchise_id"], ref["start_year"], ref["name"]
    fallback = f"{league.lower()}_franchise_{team_name.lower().replace(' ', '_')}"
    display_name = f"{city} {team_name}".strip()
    return fallback, None, display_name


def upsert_franchises(con: duckdb.DuckDBPyConnection) -> None:
    rows = [
        (ref["franchise_id"], "NFL", ref["name"], ref["start_year"])
        for ref in TEAM_REFERENCE
    ]
    con.executemany(
        """
        INSERT OR REPLACE INTO franchises (franchise_id, league, franchise_name, start_year)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def migrate(con: duckdb.DuckDBPyConnection, dry_run: bool, city_prefix_overrides: dict[str, str]) -> None:
    ensure_schema_bits(con)
    teams = fetch_team_rows(con)

    mapping = {}
    for team_id, league, city, team_name, *_ in teams:
        mapping[team_id] = team_id_for(league, city, team_name, city_prefix_overrides)

    changes = [(old_id, new_id) for old_id, new_id in mapping.items() if old_id != new_id]
    if not changes:
        print("No team_id changes required.")
    else:
        print(f"Will migrate {len(changes)} team_id values")
        for old_id, new_id in sorted(changes):
            print(f"  {old_id} -> {new_id}")

    if dry_run:
        print("Dry run only; no writes performed.")
        return

    con.execute("BEGIN")
    try:
        for old_id, new_id in changes:
            old_row = con.execute(
                """
                SELECT team_id, league, city, team_name, start_year, end_year, franchise_id
                FROM teams WHERE team_id = ?
                """,
                [old_id],
            ).fetchone()
            if not old_row:
                continue

            new_row = con.execute(
                """
                SELECT team_id, league, city, team_name, start_year, end_year, franchise_id
                FROM teams WHERE team_id = ?
                """,
                [new_id],
            ).fetchone()

            con.execute(
                """
                DELETE FROM team_games tg_old
                USING team_games tg_new
                WHERE tg_old.team_id = ?
                  AND tg_new.team_id = ?
                  AND tg_old.game_id = tg_new.game_id
                """,
                [old_id, new_id],
            )
            con.execute("UPDATE team_games SET team_id = ? WHERE team_id = ?", [new_id, old_id])
            con.execute("UPDATE team_games SET opponent_team_id = ? WHERE opponent_team_id = ?", [new_id, old_id])

            con.execute(
                """
                DELETE FROM team_group_members gm_old
                USING team_group_members gm_new
                WHERE gm_old.team_id = ?
                  AND gm_new.team_id = ?
                  AND gm_old.group_id = gm_new.group_id
                """,
                [old_id, new_id],
            )
            con.execute("UPDATE team_group_members SET team_id = ? WHERE team_id = ?", [new_id, old_id])

            if new_row:
                _, n_league, n_city, n_name, n_start, n_end, n_fr = new_row
                _, o_league, o_city, o_name, o_start, o_end, o_fr = old_row
                merged = (
                    merge_nullable(n_league, o_league),
                    merge_nullable(n_city, o_city),
                    merge_nullable(n_name, o_name),
                    merge_start_year(n_start, o_start),
                    merge_nullable(n_end, o_end),
                    merge_nullable(n_fr, o_fr),
                    new_id,
                )
                con.execute(
                    """
                    UPDATE teams
                    SET league = ?, city = ?, team_name = ?, start_year = ?, end_year = ?, franchise_id = ?
                    WHERE team_id = ?
                    """,
                    list(merged),
                )
                con.execute("DELETE FROM teams WHERE team_id = ?", [old_id])
            else:
                con.execute("UPDATE teams SET team_id = ? WHERE team_id = ?", [new_id, old_id])

        # Backfill/normalize franchise metadata and start years.
        all_teams = fetch_team_rows(con)
        for team_id, league, city, team_name, start_year, _, existing_franchise in all_teams:
            franchise_id, canonical_start_year, franchise_name = franchise_for(league, city, team_name)
            con.execute(
                "UPDATE teams SET start_year = ?, franchise_id = ? WHERE team_id = ?",
                [merge_start_year(start_year, canonical_start_year), merge_nullable(existing_franchise, franchise_id), team_id],
            )
            con.execute(
                """
                INSERT OR REPLACE INTO franchises (franchise_id, league, franchise_name, start_year)
                VALUES (?, ?, ?, ?)
                """,
                [franchise_id, league, franchise_name, canonical_start_year],
            )

        upsert_franchises(con)

        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def print_post_summary(con: duckdb.DuckDBPyConnection) -> None:
    print("\nPost-migration summary")
    print("teams:", con.execute("SELECT COUNT(*) FROM teams").fetchone()[0])
    print("franchises:", con.execute("SELECT COUNT(*) FROM franchises").fetchone()[0])
    print("team_games:", con.execute("SELECT COUNT(*) FROM team_games").fetchone()[0])
    dupes = con.execute(
        """
        SELECT league, lower(city), lower(team_name), COUNT(*) c
        FROM teams
        GROUP BY 1,2,3
        HAVING c > 1
        """
    ).fetchall()
    print("duplicate league+city+team_name rows:", len(dupes))


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    overrides = load_city_prefix_overrides(args.city_prefix_overrides, league="NFL")
    migrate(con, args.dry_run, overrides)
    if not args.dry_run:
        print_post_summary(con)


if __name__ == "__main__":
    main()
