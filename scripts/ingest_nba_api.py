#!/usr/bin/env python3
"""Ingest NBA game results via the nba_api package (stats.nba.com).

Fetches all completed regular season and playoff games from the season containing
the latest result in the DB through the current season. Safe to re-run —
uses INSERT OR REPLACE.

Usage:
    python scripts/ingest_nba_api.py
    python scripts/ingest_nba_api.py --from-season 2025-26
    python scripts/ingest_nba_api.py --db local_data/whenwin.duckdb
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from nba_api.stats.endpoints import leaguegamelog
from nba_api.stats.static import teams as nba_teams_static

from api_utils import DEFAULT_DB, DEFAULT_SCHEMA, connect, latest_result_date, resolve_team_id, upsert_games

# nba_api season type strings
SEASON_TYPES = [
    ("Regular Season", "regular"),
    ("Playoffs", "postseason"),
]

# Maps NBA abbreviation -> (city, team_name) for cases that don't match our teams table directly.
ABBREV_OVERRIDES: dict[str, tuple[str, str]] = {
    "BKN": ("Brooklyn", "Nets"),
    "CHA": ("Charlotte", "Hornets"),
    "GSW": ("Golden State", "Warriors"),
    "NOP": ("New Orleans", "Pelicans"),
    "NYK": ("New York", "Knicks"),
    "OKC": ("Oklahoma City", "Thunder"),
    "PHX": ("Phoenix", "Suns"),
    "SAS": ("San Antonio", "Spurs"),
    "UTA": ("Utah", "Jazz"),
    "WSH": ("Washington", "Wizards"),
    "MEM": ("Memphis", "Grizzlies"),
    "NJN": ("New Jersey", "Nets"),
    "SEA": ("Seattle", "SuperSonics"),
    "VAN": ("Vancouver", "Grizzlies"),
    "NOH": ("New Orleans", "Hornets"),
    "NOK": ("New Orleans/Oklahoma City", "Hornets"),
}

# Build abbrev -> nba_api team id lookup once
_NBA_STATIC_BY_ABBREV = {t["abbreviation"]: t for t in nba_teams_static.get_teams()}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument(
        "--from-season",
        default=None,
        help="First season to fetch, e.g. '2024-25'. Defaults to season of latest result.",
    )
    p.add_argument(
        "--to-season",
        default=None,
        help="Last season to fetch, e.g. '2025-26'. Defaults to current season.",
    )
    return p.parse_args()


def current_nba_season() -> str:
    """Return the NBA season string for today's date, e.g. '2025-26'."""
    today = date.today()
    # NBA season starts in October; if before October use prior year as start
    start_year = today.year if today.month >= 10 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def date_to_nba_season(d: date) -> str:
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def nba_season_end_year(season_str: str) -> int:
    """'2025-26' -> 2026"""
    return int("20" + season_str.split("-")[1])


def seasons_between(start: str, end: str) -> list[str]:
    """Return list of season strings from start to end inclusive."""
    def to_year(s: str) -> int:
        return int(s.split("-")[0])
    result = []
    y = to_year(start)
    end_y = to_year(end)
    while y <= end_y:
        result.append(f"{y}-{str(y + 1)[-2:]}")
        y += 1
    return result


def abbrev_to_city_name(abbrev: str) -> tuple[str, str]:
    if abbrev in ABBREV_OVERRIDES:
        return ABBREV_OVERRIDES[abbrev]
    static = _NBA_STATIC_BY_ABBREV.get(abbrev)
    if static:
        full = static["full_name"]  # e.g. "Los Angeles Lakers"
        nickname = static["nickname"]  # e.g. "Lakers"
        city = full[: -len(nickname)].strip()
        return city, nickname
    raise ValueError(f"Unknown NBA abbreviation: {abbrev!r}")


def fetch_season(season_str: str) -> list[dict]:
    """Fetch all game log rows for a season (both regular and playoffs)."""
    rows = []
    for api_type, _ in SEASON_TYPES:
        time.sleep(0.7)  # be polite to stats.nba.com
        gl = leaguegamelog.LeagueGameLog(
            season=season_str,
            season_type_all_star=api_type,
            direction="ASC",
        )
        df = gl.get_data_frames()[0]
        for _, row in df.iterrows():
            rows.append({
                "game_id_api": row["GAME_ID"],
                "game_date": row["GAME_DATE"],
                "team_abbrev": row["TEAM_ABBREVIATION"],
                "wl": row["WL"],
                "pts": int(row["PTS"]) if row["PTS"] else None,
                "matchup": row["MATCHUP"],
                "season_type": api_type,
            })
    return rows


def main() -> None:
    args = parse_args()
    con = connect(args.db, args.schema)

    if args.from_season:
        start_season = args.from_season
    else:
        latest = latest_result_date(con, "NBA")
        start_season = date_to_nba_season(latest) if latest else "1978-79"

    end_season = args.to_season or current_nba_season()
    season_list = seasons_between(start_season, end_season)

    print(f"Fetching NBA seasons: {', '.join(season_list)}")

    team_cache: dict = {}
    total_inserted = 0
    total_skipped = 0

    for season_str in season_list:
        season_year = nba_season_end_year(season_str)
        print(f"  Season {season_str}...", end=" ", flush=True)
        raw_rows = fetch_season(season_str)

        # Group rows by game_id so we can pair home/away
        by_game: dict[str, list[dict]] = {}
        for r in raw_rows:
            by_game.setdefault(r["game_id_api"], []).append(r)

        season_rows: list[tuple] = []
        skipped = 0

        for api_game_id, game_rows in by_game.items():
            if len(game_rows) != 2:
                skipped += 1
                continue

            r0, r1 = game_rows[0], game_rows[1]

            # Determine game_type from season_type (both rows will agree)
            raw_type = r0["season_type"]
            game_type = "postseason" if raw_type == "Playoffs" else "regular"

            pts0 = r0["pts"]
            pts1 = r1["pts"]
            if pts0 is None or pts1 is None or r0["wl"] not in ("W", "L") or r1["wl"] not in ("W", "L"):
                skipped += 1
                continue

            game_date = date.fromisoformat(r0["game_date"])

            try:
                city0, name0 = abbrev_to_city_name(r0["team_abbrev"])
                city1, name1 = abbrev_to_city_name(r1["team_abbrev"])
                tid0 = resolve_team_id(con, "NBA", season_year, city0, name0, team_cache)
                tid1 = resolve_team_id(con, "NBA", season_year, city1, name1, team_cache)
            except ValueError as e:
                print(f"\n    SKIP: {e}")
                skipped += 1
                continue

            game_id = f"nba_{api_game_id}"

            season_rows.append((game_id, game_date, "NBA", season_year, tid0, tid1, r0["wl"], pts0, pts1, game_type))
            season_rows.append((game_id, game_date, "NBA", season_year, tid1, tid0, r1["wl"], pts1, pts0, game_type))

        inserted = upsert_games(con, season_rows)
        total_inserted += inserted
        total_skipped += skipped
        print(f"{inserted} games")

    print(f"Done. Inserted {total_inserted} NBA games total, skipped {total_skipped}")


if __name__ == "__main__":
    main()
