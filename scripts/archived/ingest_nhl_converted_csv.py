#!/usr/bin/env python3
"""Ingest converted NHL season CSVs (1976-2015) into team_games.

Input files: raw/nhl/converted/YYYY-YYYY.csv
Row format (example):
  Season,Date,Visitor,Home,G-V(60),G-H(60),G-V,G-H,OT/SO

Key points:
- These files appear to represent regular season games (no explicit playoff marker).
- `season` in team_games follows the project's NHL convention: season *ending year*
  (e.g., 1981-1982 => season=1982).
- Team mapping is done against the existing `teams` table (league='NHL') using
  full team name strings (e.g., "Detroit Red Wings") and season-year containment.
- Some historical names need aliases (e.g., "Mighty Ducks of Anaheim").
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class TeamEra:
    key: str  # normalized full name key
    team_id: str
    start_year: int
    end_year: int | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="local_data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--dir", default="/Users/aric/code/whenwin/raw/nhl/converted")
    p.add_argument(
        "--from-start-year",
        type=int,
        default=1976,
        help="Start year from filename (e.g., 1976 in 1976-1977.csv)",
    )
    p.add_argument(
        "--to-start-year",
        type=int,
        default=2014,
        help="Start year from filename (e.g., 2014 in 2014-2015.csv)",
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing NHL regular-season rows for seasons ingested",
    )
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


ALIASES: dict[str, str] = {
    # Converted CSV uses this phrasing; teams table uses "Anaheim Mighty Ducks".
    "mightyducksofanaheim": "anaheimmightyducks",
}


def apply_alias(name: str) -> str:
    k = norm_key(name)
    return ALIASES.get(k, k)


def parse_season_end(season_value: str) -> int:
    # "1981-1982" -> 1982
    m = re.search(r"(19\d{2}|20\d{2})\s*$", season_value.strip())
    if not m:
        raise ValueError(f"Could not parse season end year from {season_value!r}")
    return int(m.group(1))


def parse_years_from_filename(path: Path) -> tuple[int, int]:
    # "1976-1977.csv" -> (1976, 1977)
    m = re.match(r"^(19\d{2}|20\d{2})-(19\d{2}|20\d{2})\.csv$", path.name)
    if not m:
        raise ValueError(f"Unexpected filename format: {path.name}")
    return int(m.group(1)), int(m.group(2))


def load_team_eras(con: duckdb.DuckDBPyConnection) -> dict[str, list[TeamEra]]:
    rows = con.execute(
        """
        select team_id, city, team_name, start_year, end_year
        from teams
        where league='NHL'
        """
    ).fetchall()

    out: dict[str, list[TeamEra]] = {}
    for team_id, city, team_name, start_year, end_year in rows:
        sy = int(start_year) if start_year is not None else 0
        ey = int(end_year) if end_year is not None else None
        full = f"{city} {team_name}".strip()
        key = norm_key(full)
        out.setdefault(key, []).append(
            TeamEra(key=key, team_id=str(team_id), start_year=sy, end_year=ey)
        )

    return out


def resolve_team_id(
    full_team_name: str, season_end: int, eras_index: dict[str, list[TeamEra]]
) -> str:
    key = apply_alias(full_team_name)
    eras = eras_index.get(key)
    if not eras:
        raise ValueError(f"No NHL team match for {full_team_name!r} (key={key})")

    matches = [
        e
        for e in eras
        if e.start_year <= season_end
        and (e.end_year is None or e.end_year >= season_end)
    ]
    if not matches:
        raise ValueError(
            f"No NHL team era contains season={season_end} for {full_team_name!r}"
        )

    # If boundaries overlap (e.g., one era ends the same year another starts), pick the most recent start_year.
    best = max(matches, key=lambda e: e.start_year)
    return best.team_id


def main() -> None:
    args = parse_args()

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    eras_index = load_team_eras(con)

    dirp = Path(args.dir)
    files: list[Path] = []
    for p in sorted(dirp.glob("*.csv")):
        try:
            start, end = parse_years_from_filename(p)
        except ValueError:
            continue
        if args.from_start_year <= start <= args.to_start_year:
            files.append(p)

    if not files:
        raise SystemExit(
            f"No NHL converted files found in {dirp} for start-year range {args.from_start_year}-{args.to_start_year}"
        )

    seasons_seen: set[int] = set()
    for p in files:
        _, end = parse_years_from_filename(p)
        seasons_seen.add(end)

    if args.replace and seasons_seen:
        con.execute(
            "DELETE FROM team_games WHERE league='NHL' AND game_type='regular' AND season IN (SELECT * FROM UNNEST(?))",
            [sorted(seasons_seen)],
        )

    rows_to_insert: list[tuple] = []
    inserted_games = 0

    # Disambiguate any (very unlikely) same-day repeats.
    ordinals: dict[tuple[int, str, str, str], int] = {}

    for p in files:
        with p.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            required = {"Season", "Date", "Visitor", "Home", "G-V", "G-H"}
            missing = required - set(r.fieldnames or [])
            if missing:
                raise ValueError(f"{p} missing columns: {sorted(missing)}")

            for row in r:
                season_end = parse_season_end(row["Season"])
                date = row["Date"].strip()
                # validate date format early
                datetime.strptime(date, "%Y-%m-%d")

                visitor_name = row["Visitor"].strip()
                home_name = row["Home"].strip()

                visitor_team_id = resolve_team_id(visitor_name, season_end, eras_index)
                home_team_id = resolve_team_id(home_name, season_end, eras_index)

                v_goals = int(row["G-V"])
                h_goals = int(row["G-H"])

                if v_goals > h_goals:
                    v_res, h_res = "W", "L"
                elif v_goals < h_goals:
                    v_res, h_res = "L", "W"
                else:
                    v_res = h_res = "T"

                key = (season_end, date, visitor_team_id, home_team_id)
                n = ordinals.get(key, 0) + 1
                ordinals[key] = n
                suffix = "" if n == 1 else f"_g{n}"
                game_id = f"nhl_{season_end}_{date}_{visitor_team_id}_at_{home_team_id}{suffix}"

                rows_to_insert.append(
                    (
                        game_id,
                        date,
                        "NHL",
                        season_end,
                        visitor_team_id,
                        home_team_id,
                        v_res,
                        v_goals,
                        h_goals,
                        "regular",
                    )
                )
                rows_to_insert.append(
                    (
                        game_id,
                        date,
                        "NHL",
                        season_end,
                        home_team_id,
                        visitor_team_id,
                        h_res,
                        h_goals,
                        v_goals,
                        "regular",
                    )
                )
                inserted_games += 1

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    print(
        f"Inserted {inserted_games} NHL games ({len(rows_to_insert)} team-game rows) from converted CSVs"
    )
    if seasons_seen:
        print(
            f"Seasons(end-year): {min(seasons_seen)}..{max(seasons_seen)} ({len(seasons_seen)} files)"
        )


if __name__ == "__main__":
    main()
