#!/usr/bin/env python3
"""Ingest NHL game results from the official NHL web API (api-web.nhle.com).

Fetches all completed games from the day after the latest result already in the
DB through today. Safe to re-run — uses INSERT OR REPLACE.

Usage:
    python scripts/ingest_nhl_api.py
    python scripts/ingest_nhl_api.py --from-date 2026-01-01
    python scripts/ingest_nhl_api.py --db local_data/whenwin.duckdb
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import requests

from api_utils import DEFAULT_DB, DEFAULT_SCHEMA, connect, latest_result_date, resolve_team_id, upsert_games

# Maps NHL API abbreviation -> (city, team_name) matching our teams table.
# Only entries that don't resolve cleanly from placeName/commonName need to be here.
ABBREV_OVERRIDES: dict[str, tuple[str, str]] = {
    "TBL": ("Tampa Bay", "Lightning"),
    "NJD": ("New Jersey", "Devils"),
    "LAK": ("Los Angeles", "Kings"),
    "SJS": ("San Jose", "Sharks"),
    "VGK": ("Vegas", "Golden Knights"),
    "UTA": ("Utah", "Mammoth"),
    "MTL": ("Montreal", "Canadiens"),
}

# gameType values in the NHL API
GAME_TYPE_REGULAR = 2
GAME_TYPE_PLAYOFFS = 3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--from-date", default=None, help="Override start date (YYYY-MM-DD)")
    p.add_argument("--to-date", default=None, help="Override end date (YYYY-MM-DD), defaults to today")
    return p.parse_args()


def nhl_season_end_year(season_code: int) -> int:
    """20252026 -> 2026"""
    return season_code % 10000


def api_team_to_city_name(team: dict) -> tuple[str, str]:
    abbrev = team["abbrev"]
    if abbrev in ABBREV_OVERRIDES:
        return ABBREV_OVERRIDES[abbrev]
    city = team["placeName"]["default"]
    name = team["commonName"]["default"]
    return city, name


def fetch_schedule_week(start_date: date, session: requests.Session) -> list[dict]:
    """Fetch up to one week of schedule data starting at start_date."""
    url = f"https://api-web.nhle.com/v1/schedule/{start_date.isoformat()}"
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    games = []
    for day in data.get("gameWeek", []):
        for g in day.get("games", []):
            games.append(g)
    return games


def main() -> None:
    args = parse_args()
    con = connect(args.db, args.schema)

    if args.from_date:
        start = date.fromisoformat(args.from_date)
    else:
        latest = latest_result_date(con, "NHL")
        start = (latest + timedelta(days=1)) if latest else date(1978, 10, 1)

    end = date.fromisoformat(args.to_date) if args.to_date else date.today()

    if start > end:
        print(f"NHL already up to date through {end.isoformat()}")
        return

    print(f"Fetching NHL games {start.isoformat()} → {end.isoformat()}")

    session = requests.Session()
    session.headers["User-Agent"] = "whenwin-ingest/1.0"

    team_cache: dict = {}
    all_rows: list[tuple] = []
    skipped = 0
    current = start

    while current <= end:
        games = fetch_schedule_week(current, session)
        time.sleep(0.3)

        for g in games:
            game_date = date.fromisoformat(g["startTimeUTC"][:10])
            if game_date < start or game_date > end:
                continue

            game_type_code = g.get("gameType")
            if game_type_code == GAME_TYPE_REGULAR:
                game_type = "regular"
            elif game_type_code == GAME_TYPE_PLAYOFFS:
                game_type = "postseason"
            else:
                skipped += 1
                continue

            # Only process finished games
            if g.get("gameState") not in ("OFF", "FINAL"):
                skipped += 1
                continue

            away = g["awayTeam"]
            home = g["homeTeam"]
            away_score = away.get("score")
            home_score = home.get("score")
            if away_score is None or home_score is None:
                skipped += 1
                continue

            season = nhl_season_end_year(g["season"])

            try:
                away_city, away_name = api_team_to_city_name(away)
                home_city, home_name = api_team_to_city_name(home)
                away_id = resolve_team_id(con, "NHL", season, away_city, away_name, team_cache)
                home_id = resolve_team_id(con, "NHL", season, home_city, home_name, team_cache)
            except ValueError as e:
                print(f"  SKIP: {e}")
                skipped += 1
                continue

            if away_score > home_score:
                away_res, home_res = "W", "L"
            elif home_score > away_score:
                away_res, home_res = "L", "W"
            else:
                away_res = home_res = "T"

            game_id = f"nhl_{season}_{game_type}_{game_date}_{away_id}_{home_id}"

            all_rows.append((game_id, game_date, "NHL", season, away_id, home_id, away_res, away_score, home_score, game_type))
            all_rows.append((game_id, game_date, "NHL", season, home_id, away_id, home_res, home_score, away_score, game_type))

        # Advance by 7 days (the API returns a week at a time)
        current += timedelta(days=7)

    inserted = upsert_games(con, all_rows)
    print(f"Inserted {inserted} NHL games ({len(all_rows)} team-game rows), skipped {skipped}")


if __name__ == "__main__":
    main()
