#!/usr/bin/env python3
"""Ingest Hockey-Reference NHL game logs into DuckDB.

Expected input files in a directory:
- <season>.csv (regular season)
- <season>_playoffs.csv (postseason)
You can also ingest a directory containing playoff CSVs named <season>.csv by using
--force-game-type postseason.

Columns (as exported from hockey-reference):
Date, Time, Visitor, G, Home, G, <OT/SO marker>, Att., LOG, Notes

This script normalizes each real game into two rows in `team_games`.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import duckdb

MULTIWORD_MASCOTS = {
    "Maple Leafs",
    "Blue Jackets",
    "Golden Knights",
    "Red Wings",
    "Hockey Club",
    "Black Hawks",
    "North Stars",
}

# Normalize historical/alternate naming in game logs to your canonical `teams` identities.
TEAM_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("Utah", "Hockey Club"): ("Utah", "Mammoth"),
    # Hockey-Reference sometimes uses inverted naming for this franchise.
    ("Mighty Ducks of", "Anaheim"): ("Anaheim", "Mighty Ducks"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--dir", default="raw/nhl/hockey-reference")
    p.add_argument("--min-season", type=int, default=None, help="Only ingest seasons >= this (season is end-year, e.g. 2015)")
    p.add_argument("--max-season", type=int, default=None, help="Only ingest seasons <= this (season is end-year, e.g. 2019)")
    p.add_argument("--replace", action="store_true", help="Delete NHL rows for seasons present in input")
    p.add_argument(
        "--force-game-type",
        default=None,
        choices=["regular", "postseason"],
        help="Override game_type for all ingested files (useful for playoff-only dirs)",
    )
    p.add_argument(
        "--skip-notes-regex",
        default=r"(?i)\b(suspend(?:ed)?|cancel(?:ed|led)?)\b",
        help="If Notes matches this regex, skip the game row (outcome does not count).",
    )
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
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


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def split_team(full_name: str) -> tuple[str, str]:
    full = clean_text(full_name)
    for mascot in sorted(MULTIWORD_MASCOTS, key=len, reverse=True):
        if full.endswith(" " + mascot):
            return full[: -len(mascot)].strip(), mascot
    parts = full.split(" ")
    if len(parts) < 2:
        return full, full
    return " ".join(parts[:-1]), parts[-1]


def resolve_team_id(
    con: duckdb.DuckDBPyConnection,
    season: int,
    full_team_name: str,
    cache: dict[tuple[int, str, str], str],
) -> str:
    city, mascot = split_team(full_team_name)
    city, mascot = TEAM_ALIASES.get((city, mascot), (city, mascot))
    key = (season, city, mascot)
    cached = cache.get(key)
    if cached:
        return cached

    row = con.execute(
        """
        SELECT team_id
        FROM teams
        WHERE league = 'NHL'
          AND city = ?
          AND team_name = ?
          AND start_year <= ?
          AND (end_year IS NULL OR end_year >= ?)
        ORDER BY start_year DESC
        LIMIT 1
        """,
        [city, mascot, season, season],
    ).fetchone()
    if not row:
        raise ValueError(
            f"Could not resolve NHL team_id for season={season} team={full_team_name!r} "
            f"(parsed city={city!r}, team_name={mascot!r}). "
            f"Add/adjust an identity row in raw/nhl/hockey-reference/nhl_teams.csv and re-run "
            f"python scripts/import_teams_csv.py --csv raw/nhl/hockey-reference/nhl_teams.csv."
        )
    cache[key] = row[0]
    return row[0]


def file_season_and_type(path: Path) -> tuple[int, str]:
    # Support filenames like:
    # - 2015.csv
    # - 2015_playoffs.csv
    # - 2026_04_16.csv (dated snapshot for an in-progress season)
    stem = path.stem  # no .csv
    game_type = "postseason" if stem.endswith("_playoffs") else "regular"
    if stem.endswith("_playoffs"):
        stem = stem[: -len("_playoffs")]
    m = re.match(r"^(\d{4})(?:_\d{2}_\d{2})?$", stem)
    if not m:
        raise ValueError(f"Unexpected file name: {path.name}")
    season = int(m.group(1))
    return season, game_type


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z]+", "", value.strip().lower())


def main() -> None:
    args = parse_args()
    base_dir = Path(args.dir)

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    all_files = sorted([p for p in base_dir.glob("*.csv")])
    files: list[Path] = []
    for f in all_files:
        try:
            season, _ = file_season_and_type(f)
        except ValueError:
            continue
        if args.min_season is not None and season < args.min_season:
            continue
        if args.max_season is not None and season > args.max_season:
            continue
        files.append(f)
    if not files:
        raise SystemExit(f"No season CSV files found in {base_dir} after applying season filters")

    seasons_in_files: set[int] = set()
    for f in files:
        season, _ = file_season_and_type(f)
        seasons_in_files.add(season)

    if args.replace and seasons_in_files:
        con.execute(
            "DELETE FROM team_games WHERE league = 'NHL' AND season IN (SELECT * FROM UNNEST(?))",
            [sorted(seasons_in_files)],
        )

    game_rows: list[tuple] = []
    inserted_games = 0
    skipped = 0
    team_cache: dict[tuple[int, str, str], str] = {}
    notes_skip_re = re.compile(args.skip_notes_regex) if args.skip_notes_regex else None

    for f in files:
        season, detected_game_type = file_season_and_type(f)
        game_type = args.force_game_type or detected_game_type

        with f.open("r", encoding="utf-8", newline="") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, None)
            if not header:
                continue

            # Hockey-Reference exports can have duplicate column labels ("G" twice), and at least
            # one historical file has a truncated "Date" header ("ate"). Treat column 0 as Date.
            h = [_norm_header(x) for x in header]
            i_date = 0
            i_visitor = h.index("visitor") if "visitor" in h else 2
            i_home = h.index("home") if "home" in h else 4
            i_notes = h.index("notes") if "notes" in h else None

            g_idxs = [i for i, v in enumerate(h) if v == "g"]
            i_vg = next((i for i in g_idxs if i > i_visitor and i < i_home), 3)
            i_hg = next((i for i in g_idxs if i > i_home), 5)

            for row in reader:
                if not row or all(not c.strip() for c in row):
                    continue

                if len(row) <= max(i_date, i_visitor, i_home, i_vg, i_hg, (i_notes or 0)):
                    skipped += 1
                    continue

                date = row[i_date].strip()
                visitor_full = row[i_visitor].strip()
                visitor_g = row[i_vg].strip()
                home_full = row[i_home].strip()
                home_g = row[i_hg].strip()
                notes = row[i_notes].strip() if i_notes is not None and i_notes < len(row) else ""

                if notes_skip_re and notes and notes_skip_re.search(notes):
                    skipped += 1
                    continue

                if not visitor_g or not home_g:
                    skipped += 1
                    continue

                visitor_team_id = resolve_team_id(con, season, visitor_full, team_cache)
                home_team_id = resolve_team_id(con, season, home_full, team_cache)

                v_goals = int(visitor_g)
                h_goals = int(home_g)

                if v_goals == h_goals:
                    v_res = h_res = "T"
                elif v_goals > h_goals:
                    v_res, h_res = "W", "L"
                else:
                    v_res, h_res = "L", "W"

                game_id = f"nhl_{season}_{game_type}_{date}_{visitor_team_id}_{home_team_id}"

                game_rows.append((game_id, date, "NHL", season, visitor_team_id, home_team_id, v_res, v_goals, h_goals, game_type))
                game_rows.append((game_id, date, "NHL", season, home_team_id, visitor_team_id, h_res, h_goals, v_goals, game_type))
                inserted_games += 1

    con.executemany(
        """
        INSERT OR REPLACE INTO team_games
            (game_id, date, league, season, team_id, opponent_team_id, result, pts_for, pts_against, game_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        game_rows,
    )

    print(f"Inserted {inserted_games} NHL games ({len(game_rows)} team-game rows)")
    if skipped:
        print(f"Skipped {skipped} rows with missing scores")


if __name__ == "__main__":
    main()
