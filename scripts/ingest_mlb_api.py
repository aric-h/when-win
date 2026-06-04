#!/usr/bin/env python3
"""Ingest MLB game results from the official MLB Stats API (statsapi.mlb.com).

Fetches all completed games from the day after the latest result already in the
DB through today. Safe to re-run — uses INSERT OR REPLACE.

Usage:
    python scripts/ingest_mlb_api.py
    python scripts/ingest_mlb_api.py --from-date 2026-05-08
    python scripts/ingest_mlb_api.py --db local_data/whenwin.duckdb
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import requests

from api_utils import DEFAULT_DB, DEFAULT_SCHEMA, connect, latest_result_date, resolve_team_id, upsert_games

BASE_URL = "https://statsapi.mlb.com/api/v1"

# Maps MLB Stats API team id -> (city, team_name) matching our teams table.
# Covers all 30 active franchises; the schedule endpoint only returns id + name,
# so locationName/teamName are unavailable and we can't rely on string parsing.
TEAM_ID_MAP: dict[int, tuple[str, str]] = {
    108: ("Los Angeles", "Angels"),
    109: ("Arizona", "Diamondbacks"),
    110: ("Baltimore", "Orioles"),
    111: ("Boston", "Red Sox"),
    112: ("Chicago", "Cubs"),
    113: ("Cincinnati", "Reds"),
    114: ("Cleveland", "Guardians"),
    115: ("Colorado", "Rockies"),
    116: ("Detroit", "Tigers"),
    117: ("Houston", "Astros"),
    118: ("Kansas City", "Royals"),
    119: ("Los Angeles", "Dodgers"),
    120: ("Washington", "Nationals"),
    121: ("New York", "Mets"),
    133: ("Sacramento", "Athletics"),
    134: ("Pittsburgh", "Pirates"),
    135: ("San Diego", "Padres"),
    136: ("Seattle", "Mariners"),
    137: ("San Francisco", "Giants"),
    138: ("St. Louis", "Cardinals"),
    139: ("Tampa Bay", "Rays"),
    140: ("Texas", "Rangers"),
    141: ("Toronto", "Blue Jays"),
    142: ("Minnesota", "Twins"),
    143: ("Philadelphia", "Phillies"),
    144: ("Atlanta", "Braves"),
    145: ("Chicago", "White Sox"),
    146: ("Miami", "Marlins"),
    147: ("New York", "Yankees"),
    158: ("Milwaukee", "Brewers"),
}

# MLB Stats API gameType codes we care about
GAME_TYPES = {
    "R": "regular",
    "F": "postseason",   # Wild Card
    "D": "postseason",   # Division Series
    "L": "postseason",   # League Championship Series
    "W": "postseason",   # World Series
}

CHUNK_DAYS = 30  # fetch in monthly chunks to keep requests manageable


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--from-date", default=None, help="Override start date (YYYY-MM-DD)")
    p.add_argument("--to-date", default=None, help="Override end date (YYYY-MM-DD), defaults to today")
    return p.parse_args()


def api_team_to_city_name(team_id: int) -> tuple[str, str]:
    if team_id in TEAM_ID_MAP:
        return TEAM_ID_MAP[team_id]
    raise ValueError(f"Unknown MLB API team id: {team_id}. Add it to TEAM_ID_MAP.")


def fetch_schedule(start: date, end: date, session: requests.Session) -> list[dict]:
    """Fetch all games in the date range. Returns raw game dicts from the API."""
    game_type_param = ",".join(GAME_TYPES.keys())
    url = (
        f"{BASE_URL}/schedule"
        f"?sportId=1"
        f"&startDate={start.isoformat()}"
        f"&endDate={end.isoformat()}"
        f"&gameType={game_type_param}"
        f"&hydrate=linescore"
    )
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    games = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def main() -> None:
    args = parse_args()
    con = connect(args.db, args.schema)

    if args.from_date:
        start = date.fromisoformat(args.from_date)
    else:
        latest = latest_result_date(con, "MLB")
        start = (latest + timedelta(days=1)) if latest else date(1978, 4, 1)

    end = date.fromisoformat(args.to_date) if args.to_date else date.today()

    if start > end:
        print(f"MLB already up to date through {end.isoformat()}")
        return

    print(f"Fetching MLB games {start.isoformat()} → {end.isoformat()}")

    session = requests.Session()
    session.headers["User-Agent"] = "whenwin-ingest/1.0"

    team_cache: dict = {}
    all_rows: list[tuple] = []
    skipped = 0
    chunk_start = start

    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        games = fetch_schedule(chunk_start, chunk_end, session)
        time.sleep(0.3)

        for g in games:
            status = g.get("status", {}).get("abstractGameState")
            if status != "Final":
                skipped += 1
                continue

            game_type_code = g.get("gameType")
            game_type = GAME_TYPES.get(game_type_code)
            if not game_type:
                skipped += 1
                continue

            game_date = date.fromisoformat(g["gameDate"][:10])
            season = int(g["season"])
            game_pk = g["gamePk"]

            away_data = g["teams"]["away"]
            home_data = g["teams"]["home"]
            away_score = away_data.get("score")
            home_score = home_data.get("score")

            if away_score is None or home_score is None:
                skipped += 1
                continue

            away_team = away_data["team"]
            home_team = home_data["team"]

            try:
                away_city, away_name = api_team_to_city_name(away_team["id"])
                home_city, home_name = api_team_to_city_name(home_team["id"])
                away_id = resolve_team_id(con, "MLB", season, away_city, away_name, team_cache)
                home_id = resolve_team_id(con, "MLB", season, home_city, home_name, team_cache)
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

            game_id = f"mlb_{game_pk}"

            all_rows.append((game_id, game_date, "MLB", season, away_id, home_id, away_res, away_score, home_score, game_type))
            all_rows.append((game_id, game_date, "MLB", season, home_id, away_id, home_res, home_score, away_score, game_type))

        chunk_start += timedelta(days=CHUNK_DAYS)

    inserted = upsert_games(con, all_rows)
    print(f"Inserted {inserted} MLB games ({len(all_rows)} team-game rows), skipped {skipped}")


if __name__ == "__main__":
    main()
