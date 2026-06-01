#!/usr/bin/env python3
"""Ingest Pro Football Reference style NFL CSV into team_games (two rows per game).

Important: This ingester resolves teams against the existing `teams` table so it can
handle historical identities (e.g., "Oakland Raiders", "St. Louis Rams", "Washington Redskins").

It does NOT upsert/overwrite `teams` or `franchises`.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb

WEEK_LABELS = {
    "WildCard": "wc",
    "Division": "div",
    "ConfChamp": "cc",
    "SuperBowl": "sb",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/whenwin.duckdb")
    parser.add_argument("--schema", default="sql/schema.sql")
    parser.add_argument("--csv", default="raw/nfl/2025.csv")
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--replace-season", action="store_true")
    return parser.parse_args()


def week_token(raw_week: str) -> str:
    if raw_week.isdigit():
        return f"w{int(raw_week):02d}"
    return WEEK_LABELS.get(raw_week, raw_week.lower())


def game_type_from_week(raw_week: str) -> str:
    return "regular" if raw_week.isdigit() else "postseason"

def norm_spaces(value: str) -> str:
    return " ".join(value.strip().split())


def load_team_name_index(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[str, int, int | None]]]:
    """Return mapping full_name -> list[(team_id, start_year, end_year)]."""
    rows = con.execute(
        """
        select team_id, city, team_name, start_year, end_year
        from teams
        where league='NFL'
        """
    ).fetchall()
    out: dict[str, list[tuple[str, int, int | None]]] = {}
    for team_id, city, team_name, start_year, end_year in rows:
        full = norm_spaces(f"{city} {team_name}")
        out.setdefault(full, []).append((str(team_id), int(start_year), int(end_year) if end_year is not None else None))
    return out


def resolve_team_id_from_full_name(full_name: str, season: int, index: dict[str, list[tuple[str, int, int | None]]]) -> str:
    key = norm_spaces(full_name)
    eras = index.get(key)
    if not eras:
        raise ValueError(f"Unknown team name in CSV (no match in teams table): {key!r}")
    matches = [e for e in eras if e[1] <= season and (e[2] is None or e[2] >= season)]
    if not matches:
        raise ValueError(f"No NFL team era matches name={key!r} season={season}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous NFL team era for name={key!r} season={season}: {[m[0] for m in matches]}")
    return matches[0][0]


def load_rows(con: duckdb.DuckDBPyConnection, csv_path: Path, season: int) -> tuple[list[tuple], int]:
    rows: list[tuple] = []
    skipped_unplayed = 0
    name_index = load_team_name_index(con)

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if len(header) < 10:
            raise ValueError("Unexpected CSV header format")

        for row in reader:
            if not row or all(not col.strip() for col in row):
                continue

            # PFR export columns by position:
            # 0=Week, 2=Date, 4=Winner/tie, 5='@' if winner was away, 6=Loser/tie,
            # 8=PtsW, 9=PtsL
            raw_week = row[0].strip()
            date = row[2].strip()
            winner_name = row[4].strip()
            winner_away_marker = row[5].strip()
            loser_name = row[6].strip()
            if not row[8].strip() or not row[9].strip():
                skipped_unplayed += 1
                continue

            pts_w = int(row[8])
            pts_l = int(row[9])

            winner_team_id = resolve_team_id_from_full_name(winner_name, season, name_index)
            loser_team_id = resolve_team_id_from_full_name(loser_name, season, name_index)

            # Determine away/home team_ids (PFR uses '@' marker to indicate winner was away).
            if winner_away_marker == "@":
                away_team_id = winner_team_id
                home_team_id = loser_team_id
            else:
                away_team_id = loser_team_id
                home_team_id = winner_team_id

            week = week_token(raw_week)
            game_id = f"nfl_{season}_{week}_{away_team_id}_at_{home_team_id}"
            game_type = game_type_from_week(raw_week)

            if pts_w == pts_l:
                winner_result = "T"
                loser_result = "T"
            else:
                winner_result = "W"
                loser_result = "L"

            rows.append(
                (
                    game_id,
                    date,
                    "NFL",
                    season,
                    winner_team_id,
                    loser_team_id,
                    winner_result,
                    pts_w,
                    pts_l,
                    game_type,
                )
            )
            rows.append(
                (
                    game_id,
                    date,
                    "NFL",
                    season,
                    loser_team_id,
                    winner_team_id,
                    loser_result,
                    pts_l,
                    pts_w,
                    game_type,
                )
            )

    if not rows:
        raise ValueError(f"No rows parsed from {csv_path}")
    return rows, skipped_unplayed


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    schema_path = Path(args.schema)
    csv_path = Path(args.csv)

    con = duckdb.connect(str(db_path))
    con.execute(schema_path.read_text(encoding="utf-8"))

    if args.replace_season:
        con.execute(
            "DELETE FROM team_games WHERE league = 'NFL' AND season = ?",
            [args.season],
        )

    game_rows, skipped_unplayed = load_rows(con, csv_path, args.season)
    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        game_rows,
    )

    ingested_games = len(game_rows) // 2
    print(f"Ingested NFL season {args.season}: {ingested_games} games, {len(game_rows)} team-game rows")
    if skipped_unplayed:
        print(f"Skipped {skipped_unplayed} unplayed/scheduled rows with blank scores")


if __name__ == "__main__":
    main()
