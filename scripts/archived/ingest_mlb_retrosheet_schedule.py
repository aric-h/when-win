#!/usr/bin/env python3
"""Ingest Retrosheet MLB schedules into team_games.

Important: Retrosheet *schedule* files do not contain final scores.
This ingester records participation rows (two per game) with NULL result/pts.

It enforces strict mapping:
- Every Visitor/Home code in the schedule must resolve to exactly one MLB team identity
  for that season, using raw/mlb/mlb_teams.csv.

Team identity resolution:
- For a given (retrosheet_code, season), pick the row whose [from,to] contains season.
- Team IDs are generated as: mlb_<code_lower>_<location_slug>_<team_name_slug>
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class TeamEra:
    code: str
    team_id: str
    league: str
    start_year: int
    end_year: int | None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--teams-csv", default="raw/mlb/mlb_teams.csv")
    p.add_argument("--dir", default="raw/mlb/retrosheet")
    p.add_argument("--from-year", type=int, default=1990)
    p.add_argument("--to-year", type=int, default=2025)
    p.add_argument("--replace", action="store_true", help="Delete MLB rows for seasons ingested")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_end_year(value: str) -> int | None:
    v = value.strip()
    if not v or v.upper() == "NULL":
        return None
    return int(v)


def team_id_for(code: str, team_name: str) -> str:
    raise NotImplementedError("Use team_id_for_row to ensure IDs match import_mlb_teams_csv.py")


def team_id_for_row(code: str, location: str, team_name: str) -> str:
    # Must match scripts/import_mlb_teams_csv.py
    return f"mlb_{code.lower()}_{norm(location)}_{norm(team_name)}"


def load_team_eras(path: Path) -> list[TeamEra]:
    eras: list[TeamEra] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("league", "").strip().upper() != "MLB":
                continue
            code = row["retrosheet_code"].strip().upper()
            team_name = " ".join(row["team_name"].strip().split())
            location = " ".join(row["location"].strip().split())
            start = int(row["from"].strip())
            end = parse_end_year(row["to"])
            eras.append(
                TeamEra(
                    code=code,
                    team_id=team_id_for_row(code, location, team_name),
                    league="MLB",
                    start_year=start,
                    end_year=end,
                )
            )
    return eras


def resolve_team_id(code: str, season: int, eras: list[TeamEra]) -> str:
    matches = [
        e
        for e in eras
        if e.code == code and e.start_year <= season and (e.end_year is None or e.end_year >= season)
    ]
    if not matches:
        raise ValueError(f"No MLB team era found for code={code} season={season}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous MLB team era for code={code} season={season}: {matches}")
    return matches[0].team_id


def yyyymmdd_to_date(value: str) -> str:
    v = value.strip().strip('"')
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"


def main() -> None:
    args = parse_args()

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    eras = load_team_eras(Path(args.teams_csv))

    seasons = list(range(args.from_year, args.to_year + 1))
    if args.replace:
        con.execute(
            "DELETE FROM team_games WHERE league='MLB' AND season IN (SELECT * FROM UNNEST(?))",
            [seasons],
        )

    inserted_games = 0
    skipped = 0
    unknown_codes: dict[int, set[str]] = {}
    rows_to_insert: list[tuple] = []

    for season in seasons:
        schedule_path = Path(args.dir) / f"{season}schedule.csv"
        if not schedule_path.exists():
            continue

        with schedule_path.open("r", encoding="utf-8", newline="") as f:
            # Retrosheet schedules have duplicate column names (e.g., League/Game appear twice),
            # so csv.DictReader is unsafe here. Use positional parsing instead.
            r = csv.reader(f)
            header = next(r, None)
            if not header:
                continue

            # Resolve indices from header to support slight schema variations over time.
            # Typical headers include duplicate "League" and "Game" columns.
            try:
                i_date = header.index("Date")
                i_visitor = header.index("Visitor")
                i_home = header.index("Home")
                i_postponed = header.index("Postponed")
            except ValueError as e:
                raise ValueError(f"Unexpected Retrosheet schedule header in {schedule_path}: {header}") from e

            game_idxs = [i for i, h in enumerate(header) if h == "Game"]
            if len(game_idxs) >= 2:
                i_v_game, i_h_game = game_idxs[0], game_idxs[1]
            elif len(game_idxs) == 1:
                i_v_game, i_h_game = game_idxs[0], game_idxs[0]
            else:
                i_v_game = i_h_game = -1

            for row in r:
                if not row:
                    continue
                if len(row) <= i_postponed:
                    # Malformed line; skip rather than crash mid-season.
                    skipped += 1
                    continue
                if row[i_postponed].strip():
                    skipped += 1
                    continue

                date = yyyymmdd_to_date(row[i_date])
                visitor = row[i_visitor].strip().strip('"').upper()
                home = row[i_home].strip().strip('"').upper()

                try:
                    visitor_team_id = resolve_team_id(visitor, season, eras)
                except ValueError:
                    unknown_codes.setdefault(season, set()).add(visitor)
                    continue
                try:
                    home_team_id = resolve_team_id(home, season, eras)
                except ValueError:
                    unknown_codes.setdefault(season, set()).add(home)
                    continue

                # Use a deterministic ID; doubleheaders are disambiguated by the schedule's Game numbers.
                v_game = row[i_v_game].strip() if i_v_game >= 0 and i_v_game < len(row) else ""
                h_game = row[i_h_game].strip() if i_h_game >= 0 and i_h_game < len(row) else ""
                game_id = f"mlb_{season}_{date}_{visitor}_{home}_{v_game}_{h_game}"

                rows_to_insert.append((game_id, date, "MLB", season, visitor_team_id, home_team_id, None, None, None, "regular"))
                rows_to_insert.append((game_id, date, "MLB", season, home_team_id, visitor_team_id, None, None, None, "regular"))
                inserted_games += 1

    if unknown_codes:
        details = ", ".join(f"{season}: {sorted(codes)}" for season, codes in sorted(unknown_codes.items()))
        raise SystemExit(f"Unknown retrosheet team codes encountered (season -> codes): {details}")

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    print(f"Inserted {inserted_games} MLB games ({len(rows_to_insert)} team-game rows)")
    if skipped:
        print(f"Skipped {skipped} postponed rows")


if __name__ == "__main__":
    main()
