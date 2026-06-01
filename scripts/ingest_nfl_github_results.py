#!/usr/bin/env python3
"""Ingest archived NFL game results from raw/nfl/github into team_games.

Input format (per-season CSVs):
  season,week,kickoff,home_team,home_score,visitors_score,visiting_team

Notes:
- home_team / visiting_team are nickname strings (e.g., "Rams", "Oilers", "Redskins"),
  not city abbreviations.
- Winner/loser is derived from the scores; ties become result='T' for both rows.
- game_type is derived from week counts using a per-season heuristic (regular vs postseason).

Team mapping:
- Resolve (team_name, season) to exactly one teams.team_id where league='NFL' and season is
  within [start_year, end_year] (end_year NULL treated as open-ended).

This ingester is intentionally league-specific because of the source format.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class TeamEra:
    team_name: str
    team_id: str
    start_year: int
    end_year: int | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--dir", default="raw/nfl/github")
    p.add_argument("--from-year", type=int, default=1978)
    p.add_argument("--to-year", type=int, default=2014)
    p.add_argument("--replace", action="store_true", help="Delete NFL rows for seasons ingested (both regular+postseason)")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def parse_season_from_filename(name: str) -> int | None:
    # Year embedded in filenames like "nfl 2009.csv"
    m = re.search(r"(19\d{2}|20\d{2})", name)
    return int(m.group(1)) if m else None


def kickoff_to_date(kickoff: str) -> str:
    # kickoff is ISO-ish: 2009-09-10T00:00:00+00:00
    v = kickoff.strip().strip('"')
    return v[0:10]


def parse_int(value: str) -> int:
    return int(value.strip().strip('"'))


def load_nfl_team_eras(con: duckdb.DuckDBPyConnection) -> dict[str, list[TeamEra]]:
    rows = con.execute(
        """
        select team_name, team_id, start_year, end_year
        from teams
        where league='NFL'
        """
    ).fetchall()
    by_name: dict[str, list[TeamEra]] = defaultdict(list)
    for team_name, team_id, start_year, end_year in rows:
        by_name[str(team_name)].append(TeamEra(str(team_name), str(team_id), int(start_year), int(end_year) if end_year is not None else None))
    return by_name


def resolve_team_id(team_name: str, season: int, by_name: dict[str, list[TeamEra]]) -> str:
    name = " ".join(team_name.strip().split())
    eras = by_name.get(name)
    if not eras:
        raise ValueError(f"Unknown NFL team_name='{name}' in teams table")
    matches = [e for e in eras if e.start_year <= season and (e.end_year is None or e.end_year >= season)]
    if not matches:
        raise ValueError(f"No NFL team era matches team_name='{name}' season={season}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous NFL team era for team_name='{name}' season={season}: {[m.team_id for m in matches]}")
    return matches[0].team_id


def derive_regular_season_cutoff(week_counts: Counter[int]) -> int:
    """Return last week considered regular season for this season.

    We avoid hardcoding week numbers because league structure changes and strike seasons exist.
    Heuristic:
    - Let max_games = max games in any week
    - Define threshold = max(6, floor(0.75 * max_games))
    - Regular season weeks are those with count >= threshold
    - cutoff = max regular week
    """
    if not week_counts:
        return 0
    max_games = max(week_counts.values())
    threshold = max(6, (max_games * 3) // 4)
    regular_weeks = [w for w, c in week_counts.items() if c >= threshold]
    if not regular_weeks:
        # Fallback: treat all weeks as regular.
        return max(week_counts)
    return max(regular_weeks)


def main() -> None:
    args = parse_args()

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    by_name = load_nfl_team_eras(con)

    dirp = Path(args.dir)
    files = sorted([p for p in dirp.glob("*.csv") if (s := parse_season_from_filename(p.name)) is not None and args.from_year <= s <= args.to_year])
    if not files:
        raise SystemExit(f"No NFL github CSV files found in {dirp} for range {args.from_year}-{args.to_year}")

    seasons = sorted({parse_season_from_filename(p.name) for p in files if parse_season_from_filename(p.name) is not None})
    if args.replace:
        con.execute(
            "DELETE FROM team_games WHERE league='NFL' AND season IN (SELECT * FROM UNNEST(?))",
            [seasons],
        )

    unknown_names: dict[int, set[str]] = defaultdict(set)
    inserted_games = 0
    rows_to_insert: list[tuple] = []

    for path in files:
        season = parse_season_from_filename(path.name)
        if season is None:
            continue

        # First pass: count games by week to derive regular/postseason cutoff.
        week_counts: Counter[int] = Counter()
        with path.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                week_counts[int(row["week"])] += 1
        cutoff = derive_regular_season_cutoff(week_counts)

        with path.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                week = int(row["week"])
                game_type = "regular" if week <= cutoff else "postseason"

                date = kickoff_to_date(row["kickoff"])
                home_name = row["home_team"].strip()
                away_name = row["visiting_team"].strip()

                try:
                    home_id = resolve_team_id(home_name, season, by_name)
                except ValueError:
                    unknown_names[season].add(home_name)
                    continue
                try:
                    away_id = resolve_team_id(away_name, season, by_name)
                except ValueError:
                    unknown_names[season].add(away_name)
                    continue

                home_pts = parse_int(row["home_score"])
                away_pts = parse_int(row["visitors_score"])

                if home_pts > away_pts:
                    home_res, away_res = "W", "L"
                elif home_pts < away_pts:
                    home_res, away_res = "L", "W"
                else:
                    home_res, away_res = "T", "T"

                # Include season+week to reduce collision risk if source ever contains same-day rematches (unlikely).
                game_id = f"nfl_{season}_{date}_w{week:02d}_{away_id}_at_{home_id}"

                rows_to_insert.append((game_id, date, "NFL", season, away_id, home_id, away_res, away_pts, home_pts, game_type))
                rows_to_insert.append((game_id, date, "NFL", season, home_id, away_id, home_res, home_pts, away_pts, game_type))
                inserted_games += 1

    if unknown_names:
        details = ", ".join(f"{season}: {sorted(names)}" for season, names in sorted(unknown_names.items()) if names)
        raise SystemExit(f"Unknown NFL team names encountered (season -> names): {details}")

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    print(f"Imported {inserted_games} NFL games ({len(rows_to_insert)} team-game rows) from github CSVs")


if __name__ == "__main__":
    main()
