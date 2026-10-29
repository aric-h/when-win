#!/usr/bin/env python3
"""Ingest NFL game results from the nflverse/nfldata games.csv (GitHub, no auth).

NFL has no official free API (see CLAUDE.md). nflverse/nfldata publishes a
community-maintained games.csv updated weekly during the season, covering
1999-present with final scores. Fetches all completed games from the day
after the latest result already in the DB through today. Safe to re-run —
uses INSERT OR REPLACE.

After running this, run scripts/ingest_postseason_metadata.py --league NFL
to refresh round labels and series-clinch flags.

Usage:
    python scripts/ingest_nfl.py
    python scripts/ingest_nfl.py --from-date 2026-09-01
    python scripts/ingest_nfl.py --db local_data/whenwin.duckdb
"""

from __future__ import annotations

import argparse
import csv
import io
from datetime import date, timedelta

import requests

from api_utils import DEFAULT_DB, DEFAULT_SCHEMA, connect, latest_result_date, resolve_team_id, upsert_games

GAMES_CSV_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# nflverse team abbreviation -> (city, team_name) matching our teams table.
# Current franchise locations/names only — this script is forward-fill only
# (see latest_result_date fallback below), so pre-relocation eras (e.g. San
# Diego Chargers, Oakland Raiders, Washington Redskins) are never looked up.
TEAM_ABBREV_MAP: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LA": ("Los Angeles", "Rams"),
    "LAC": ("Los Angeles", "Chargers"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

# Our game_id convention abbreviates nflverse codes, lowercased, except the
# Rams (nflverse "LA" -> our "lar"; "LAC" stays "lac" for the Chargers).
GAME_ID_TOKEN_OVERRIDES: dict[str, str] = {"LA": "lar"}

# nflverse game_type -> (our game_type, round token for postseason game_ids).
# Round tokens match NFL_TOKEN_MAP in ingest_postseason_metadata.py.
GAME_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "REG": ("regular", None),
    "WC": ("postseason", "wc"),
    "DIV": ("postseason", "div"),
    "CON": ("postseason", "cc"),
    "SB": ("postseason", "sb"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--from-date", default=None, help="Override start date (YYYY-MM-DD)")
    p.add_argument("--to-date", default=None, help="Override end date (YYYY-MM-DD), defaults to today")
    return p.parse_args()


def game_id_token(abbrev: str) -> str:
    return GAME_ID_TOKEN_OVERRIDES.get(abbrev, abbrev.lower())


def fetch_games(session: requests.Session) -> list[dict]:
    resp = session.get(GAMES_CSV_URL, timeout=30)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def main() -> None:
    args = parse_args()
    con = connect(args.db, args.schema)

    if args.from_date:
        start = date.fromisoformat(args.from_date)
    else:
        latest = latest_result_date(con, "NFL")
        # nflverse games.csv only goes back to 1999; earlier seasons were
        # backfilled separately (see scripts/archived/).
        start = (latest + timedelta(days=1)) if latest else date(1999, 9, 1)

    end = date.fromisoformat(args.to_date) if args.to_date else date.today()

    if start > end:
        print(f"NFL already up to date through {end.isoformat()}")
        return

    print(f"Fetching NFL games {start.isoformat()} → {end.isoformat()}")

    session = requests.Session()
    session.headers["User-Agent"] = "whenwin-ingest/1.0"

    games = fetch_games(session)

    team_cache: dict = {}
    all_rows: list[tuple] = []
    skipped = 0

    for g in games:
        if not g.get("gameday"):
            continue
        game_date = date.fromisoformat(g["gameday"])
        if game_date < start or game_date > end:
            continue

        away_score_raw = g.get("away_score")
        home_score_raw = g.get("home_score")
        if not away_score_raw or not home_score_raw:
            skipped += 1  # not yet played
            continue

        type_info = GAME_TYPE_MAP.get(g.get("game_type", ""))
        if not type_info:
            skipped += 1
            continue
        game_type, round_token = type_info

        season = int(g["season"])
        away_abbrev = g["away_team"]
        home_abbrev = g["home_team"]

        try:
            away_city, away_name = TEAM_ABBREV_MAP[away_abbrev]
            home_city, home_name = TEAM_ABBREV_MAP[home_abbrev]
            away_id = resolve_team_id(con, "NFL", season, away_city, away_name, team_cache)
            home_id = resolve_team_id(con, "NFL", season, home_city, home_name, team_cache)
        except (KeyError, ValueError) as e:
            print(f"  SKIP: {e}")
            skipped += 1
            continue

        away_score = int(away_score_raw)
        home_score = int(home_score_raw)

        if away_score > home_score:
            away_res, home_res = "W", "L"
        elif home_score > away_score:
            away_res, home_res = "L", "W"
        else:
            away_res = home_res = "T"

        away_token = game_id_token(away_abbrev)
        home_token = game_id_token(home_abbrev)
        if game_type == "regular":
            game_id = f"nfl_{season}_w{int(g['week']):02d}_{away_token}_{home_token}"
        else:
            game_id = f"nfl_{season}_{round_token}_{away_token}_{home_token}"

        all_rows.append((game_id, game_date, "NFL", season, away_id, home_id, away_res, away_score, home_score, game_type))
        all_rows.append((game_id, game_date, "NFL", season, home_id, away_id, home_res, home_score, away_score, game_type))

    inserted = upsert_games(con, all_rows)
    print(f"Inserted {inserted} NFL games ({len(all_rows)} team-game rows), skipped {skipped}")


if __name__ == "__main__":
    main()
