#!/usr/bin/env python3
"""Ingest Retrosheet MLB *game logs* (glYYYY.txt) into team_games.

Retrosheet game logs include final score, so we can populate:
- result (W/L)
- pts_for / pts_against (runs)

Strict mapping:
- Every visitor/home code must resolve to exactly one MLB team identity for that season
  using raw/mlb/mlb_teams.csv (retrosheet_code + [from,to] era resolution).

Team IDs must match scripts/import_mlb_teams_csv.py:
  mlb_<code_lower>_<location_slug>_<team_name_slug>
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
    p.add_argument(
        "--dir",
        default="raw/mlb/retrosheet/game_logs",
        help="Directory containing glYYYY.txt files (e.g., gl2020.txt)",
    )
    p.add_argument("--from-year", type=int, default=2020)
    p.add_argument("--to-year", type=int, default=2025)
    p.add_argument(
        "--game-type",
        choices=["regular", "postseason"],
        default="regular",
        help="Set game_type for all rows ingested from these files",
    )
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
            "DELETE FROM team_games WHERE league='MLB' AND game_type = ? AND season IN (SELECT * FROM UNNEST(?))",
            [args.game_type, seasons],
        )

    inserted_games = 0
    unknown_codes: dict[int, set[str]] = {}
    rows_to_insert: list[tuple] = []

    for season in seasons:
        gl_path = Path(args.dir) / f"gl{season}.txt"
        if not gl_path.exists():
            continue

        with gl_path.open("r", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            for row in r:
                if not row:
                    continue
                # Retrosheet GL format is positional (no header in these files).
                # 0 date (YYYYMMDD)
                # 3 visiting team (3-letter code)
                # 5 visiting game number (for doubleheaders, etc.)
                # 6 home team (3-letter code)
                # 8 home game number
                # 9 visiting runs, 10 home runs
                if len(row) < 11:
                    continue

                date_yyyymmdd = row[0].strip().strip('"')
                if not date_yyyymmdd or len(date_yyyymmdd) != 8:
                    continue
                # Some seasons can have games in the next calendar year (e.g., rare) but we
                # treat "season" as the file's year for now. If this becomes an issue, we can
                # derive season from the date instead.
                date = yyyymmdd_to_date(date_yyyymmdd)
                visitor = row[3].strip().strip('"').upper()
                home = row[6].strip().strip('"').upper()

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

                try:
                    v_runs = int(row[9])
                    h_runs = int(row[10])
                except ValueError:
                    continue

                v_game = row[5].strip()
                h_game = row[8].strip()
                game_id = f"mlb_{season}_{date}_{visitor}_{home}_{v_game}_{h_game}"

                if v_runs > h_runs:
                    v_res, h_res = "W", "L"
                else:
                    v_res, h_res = "L", "W"

                rows_to_insert.append((game_id, date, "MLB", season, visitor_team_id, home_team_id, v_res, v_runs, h_runs, args.game_type))
                rows_to_insert.append((game_id, date, "MLB", season, home_team_id, visitor_team_id, h_res, h_runs, v_runs, args.game_type))
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

    print(f"Inserted {inserted_games} MLB games ({len(rows_to_insert)} team-game rows) from game logs")


if __name__ == "__main__":
    main()
