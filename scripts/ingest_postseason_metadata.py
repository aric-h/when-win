#!/usr/bin/env python3
"""Populate postseason_game_rounds, postseason_series, and is_series_clinching
for all four leagues from their respective APIs / game_id encodings.

Safe to re-run — upserts throughout. Run after game ingestion scripts so all
game rows exist before metadata is written.

Usage:
    python scripts/ingest_postseason_metadata.py
    python scripts/ingest_postseason_metadata.py --league NHL
    python scripts/ingest_postseason_metadata.py --from-season 2020
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import date

import requests

from api_utils import DEFAULT_DB, DEFAULT_SCHEMA, connect

# ---------------------------------------------------------------------------
# Round name / order tables
# ---------------------------------------------------------------------------

NHL_ROUNDS = {1: "First Round", 2: "Second Round", 3: "Conference Finals", 4: "Stanley Cup Final"}
NBA_ROUNDS = {1: "First Round", 2: "Conference Semifinals", 3: "Conference Finals", 4: "NBA Finals"}
MLB_ROUND_NAMES = {
    "Wild Card Series": (1, "Wild Card Series"),
    "AL Wild Card Series": (1, "Wild Card Series"),
    "NL Wild Card Series": (1, "Wild Card Series"),
    "Division Series": (2, "Division Series"),
    "AL Division Series": (2, "Division Series"),
    "NL Division Series": (2, "Division Series"),
    "Championship Series": (3, "Championship Series"),
    "AL Championship Series": (3, "Championship Series"),
    "NL Championship Series": (3, "Championship Series"),
    "World Series": (4, "World Series"),
    # Pre-wild-card era (single wild card / best-of-5 division series)
    "League Championship Series": (2, "League Championship Series"),
    "AL League Championship Series": (2, "League Championship Series"),
    "NL League Championship Series": (2, "League Championship Series"),
}
NFL_ROUND_TOKENS = {
    "wc": (1, "Wild Card"),
    "div": (2, "Divisional"),
    "cc": (3, "Conference Championship"),
    "conf": (3, "Conference Championship"),
    "sb": (4, "Super Bowl"),
}

# Wins needed to clinch a series by league / round (all best-of-7 except where noted)
NBA_SERIES_WINS_NEEDED = 4   # all rounds best-of-7
NHL_SERIES_WINS_NEEDED = 4   # all rounds best-of-7


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reconstruct_from_game_rounds(con, league: str, season: int,
                                  series_source: str) -> tuple[list[tuple], list[tuple], list[str], list[str]]:
    """Build postseason_series rows and identify clinching games for a league/season
    by joining team_games with the already-populated postseason_game_rounds table.

    Works for any league where postseason_game_rounds is already complete.
    Returns (series_rows, round_rows_new, clinch_game_ids, clinch_team_ids).
    round_rows_new will be empty since we're relying on existing round data.
    """
    rows = con.execute(
        """
        SELECT tg.game_id, tg.date, tg.team_id, tg.opponent_team_id,
               tg.result, pgr.round_order, pgr.round_name
        FROM team_games tg
        JOIN postseason_game_rounds pgr
          ON pgr.game_id = tg.game_id AND pgr.league = tg.league
        WHERE tg.league = ? AND tg.season = ? AND tg.game_type = 'postseason'
        ORDER BY tg.date, tg.game_id
        """,
        [league, season],
    ).fetchall()

    if not rows:
        return [], [], [], []

    # Group by (round_order, frozenset of team pair) to reconstruct series
    series_map: dict[tuple, list] = defaultdict(list)
    for game_id, game_date, team_id, opp_id, result, round_order, round_name in rows:
        key = (round_order, round_name, frozenset([team_id, opp_id]))
        series_map[key].append((game_id, game_date, team_id, opp_id, result))

    series_rows: list[tuple] = []
    clinch_game_ids: list[str] = []
    clinch_team_ids: list[str] = []
    wins_needed = {"NHL": 4, "NBA": 4, "MLB": None}  # None = variable (best-of-5 or 7)

    for idx, ((round_order, round_name, team_pair), game_list) in enumerate(series_map.items()):
        teams = sorted(team_pair)
        tid_a, tid_b = teams[0], teams[1]

        unique_game_ids = list({g[0] for g in game_list})
        dates = sorted({g[1] for g in game_list})
        series_start, series_end = dates[0], dates[-1]

        wins: dict[str, int] = defaultdict(int)
        for game_id, game_date, team_id, opp_id, result in game_list:
            if result == "W":
                wins[team_id] += 1

        max_wins = max(wins.values()) if wins else 0
        winner_tid = next((t for t, w in wins.items() if w == max_wins), None)

        # Clincher: last win by the winner
        if winner_tid:
            winner_wins = sorted(
                [g for g in game_list if g[2] == winner_tid and g[4] == "W"],
                key=lambda g: g[1],
            )
            if winner_wins:
                clinch = winner_wins[-1]
                clinch_game_ids.append(clinch[0])
                clinch_team_ids.append(winner_tid)

        series_id = f"{league.lower()}_{season}_{round_order}_{idx}"
        series_rows.append((
            league, season, series_id, tid_a, tid_b,
            series_start, series_end, len(unique_game_ids),
            round_order, round_name, series_source,
        ))

    return series_rows, [], clinch_game_ids, clinch_team_ids

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--schema", default=DEFAULT_SCHEMA)
    p.add_argument("--league", choices=["NHL", "NBA", "MLB", "NFL"], default=None,
                   help="Process only this league (default: all)")
    p.add_argument("--from-season", type=int, default=None,
                   help="Only process seasons >= this end-year (e.g. 2020)")
    return p.parse_args()


def upsert_game_rounds(con, rows: list[tuple]) -> int:
    """rows: (league, game_id, season, round_order, round_name, source)"""
    if not rows:
        return 0
    con.executemany(
        """
        INSERT OR REPLACE INTO postseason_game_rounds
            (league, game_id, season, round_order, round_name, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_series(con, rows: list[tuple]) -> int:
    """rows: (league, season, series_id, team_id_a, team_id_b,
              series_start_date, series_end_date, games_in_matchup,
              round_order, round_name, source)"""
    if not rows:
        return 0
    con.executemany(
        """
        INSERT OR REPLACE INTO postseason_series
            (league, season, series_id, team_id_a, team_id_b,
             series_start_date, series_end_date, games_in_matchup,
             round_order, round_name, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def set_series_clinching(con, game_ids: list[str], team_ids: list[str]) -> int:
    """Mark (game_id, team_id) pairs as is_series_clinching=TRUE."""
    if not game_ids:
        return 0
    pairs = list(zip(game_ids, team_ids))
    con.executemany(
        "UPDATE team_games SET is_series_clinching = TRUE WHERE game_id = ? AND team_id = ?",
        pairs,
    )
    return len(pairs)


def get_postseason_seasons(con, league: str, from_season: int | None) -> list[int]:
    rows = con.execute(
        """
        SELECT DISTINCT season FROM team_games
        WHERE league = ? AND game_type = 'postseason'
        ORDER BY season
        """,
        [league],
    ).fetchall()
    seasons = [r[0] for r in rows]
    if from_season:
        seasons = [s for s in seasons if s >= from_season]
    return seasons


# ---------------------------------------------------------------------------
# NHL
# ---------------------------------------------------------------------------

def process_nhl(con, seasons: list[int]) -> None:
    session = requests.Session()
    session.headers["User-Agent"] = "whenwin-ingest/1.0"

    total_round_rows = total_series_rows = total_clinchers = 0

    for season in seasons:
        url = f"https://api-web.nhle.com/v1/playoff-bracket/{season}"
        resp = session.get(url, timeout=10)
        time.sleep(0.3)
        if resp.status_code == 404:
            print(f"  NHL {season}: no bracket data (404)")
            continue
        resp.raise_for_status()
        bracket = resp.json()

        # Map NHL API abbrev -> our team_id for this season
        abbrev_to_id: dict[str, str] = {}
        rows_abbrev = con.execute(
            """
            SELECT t.team_id,
                   upper(substr(t.team_id, 5, 3)) as rough_abbrev,
                   t.city, t.team_name
            FROM teams t
            WHERE t.league = 'NHL'
              AND t.start_year <= ? AND (t.end_year IS NULL OR t.end_year >= ?)
            """,
            [season, season],
        ).fetchall()
        # Build a name-based lookup to match API abbrevs properly
        name_to_id: dict[str, str] = {
            r[3].lower(): r[0] for r in rows_abbrev
        }
        city_name_to_id: dict[tuple, str] = {
            (r[2].lower(), r[3].lower()): r[0] for r in rows_abbrev
        }

        # NHL API always returns commonName="Ducks" even for Mighty Ducks era seasons.
        # Applied as fallback AFTER direct lookup fails (post-2006 "Ducks" resolves directly).
        _COMMON_NAME_ALIASES = {"ducks": "mighty ducks"}

        def nhl_team_id(team_dict: dict) -> str | None:
            common = team_dict.get("commonName", {}).get("default", "").lower()
            # Direct match
            if common in name_to_id:
                return name_to_id[common]
            # Legacy name alias (e.g. API returns "Ducks" for Mighty Ducks era)
            aliased = _COMMON_NAME_ALIASES.get(common, common)
            if aliased != common and aliased in name_to_id:
                return name_to_id[aliased]
            # Normalised match: strip spaces (handles "Blackhawks" vs "Black Hawks")
            common_nospace = common.replace(" ", "").replace("-", "")
            for name_key, tid in name_to_id.items():
                if name_key.replace(" ", "").replace("-", "") == common_nospace:
                    return tid
            # City+name combo
            for (city, name), tid in city_name_to_id.items():
                if name.replace(" ", "") == common_nospace:
                    return tid
            return None

        round_rows: list[tuple] = []
        series_rows: list[tuple] = []
        clinch_game_ids: list[str] = []
        clinch_team_ids: list[str] = []

        for s in bracket.get("series", []):
            round_order = s.get("playoffRound")
            round_name = NHL_ROUNDS.get(round_order, f"Round {round_order}")
            series_letter = s.get("seriesLetter", "")

            top_team = s.get("topSeedTeam") or s.get("bottomSeedTeam")
            bot_team = s.get("bottomSeedTeam") or s.get("topSeedTeam")
            if not top_team or not bot_team:
                continue

            tid_a = nhl_team_id(top_team)
            tid_b = nhl_team_id(bot_team)
            if not tid_a or not tid_b:
                print(f"  NHL {season} series {series_letter}: could not resolve teams "
                      f"({top_team.get('abbrev')}, {bot_team.get('abbrev')})")
                continue

            top_wins = s.get("topSeedWins", 0)
            bot_wins = s.get("bottomSeedWins", 0)
            winning_team_api_id = s.get("winningTeamId")

            # Determine winner team_id
            winner_tid: str | None = None
            if winning_team_api_id:
                if winning_team_api_id == top_team.get("id"):
                    winner_tid = tid_a
                else:
                    winner_tid = tid_b

            # Fetch all games in this season for this team pair to get dates/game_ids
            game_rows = con.execute(
                """
                SELECT DISTINCT tg.game_id, tg.date, tg.result, tg.team_id
                FROM team_games tg
                WHERE tg.league = 'NHL' AND tg.season = ?
                  AND tg.game_type = 'postseason'
                  AND tg.team_id IN (?, ?)
                  AND tg.opponent_team_id IN (?, ?)
                ORDER BY tg.date
                """,
                [season, tid_a, tid_b, tid_a, tid_b],
            ).fetchall()

            if not game_rows:
                continue

            dates = sorted({r[1] for r in game_rows})
            series_start = dates[0]
            series_end = dates[-1]
            total_games = len({r[0] for r in game_rows})

            series_id = f"nhl_{season}_{tid_a}_{tid_b}_{series_letter.lower()}"
            series_rows.append((
                "NHL", season, series_id, tid_a, tid_b,
                series_start, series_end, total_games,
                round_order, round_name, "api",
            ))

            # Round labels for each game
            for game_id, *_ in {(r[0],) for r in game_rows}:
                round_rows.append(("NHL", game_id, season, round_order, round_name, "api"))

            # Clinching game: winner's last game in the series
            if winner_tid:
                winner_wins = [r for r in game_rows if r[2] == "W" and r[3] == winner_tid]
                if winner_wins:
                    clinch_row = max(winner_wins, key=lambda r: r[1])  # latest date
                    clinch_game_ids.append(clinch_row[0])
                    clinch_team_ids.append(winner_tid)

        r1 = upsert_game_rounds(con, round_rows)
        r2 = upsert_series(con, series_rows)
        r3 = set_series_clinching(con, clinch_game_ids, clinch_team_ids)
        total_round_rows += r1
        total_series_rows += r2
        total_clinchers += r3
        print(f"  NHL {season}: {len(series_rows)} series, {r1} round labels, {r3} clinchers")

    print(f"NHL total: {total_series_rows} series, {total_round_rows} round rows, {total_clinchers} clinchers")


# ---------------------------------------------------------------------------
# NBA
# ---------------------------------------------------------------------------

NBA_ROUND_NAMES = {
    "1": (1, "First Round"),
    "2": (2, "Conference Semifinals"),
    "3": (3, "Conference Finals"),
    "4": (4, "NBA Finals"),
}
NBA_WINS_TO_CLINCH = 4  # all NBA rounds are best-of-7


def decode_nba_game_id(game_id: str) -> tuple[int, int, int] | None:
    """Parse nba_XXXXXXXXXX -> (round, series_in_round, game_in_series)."""
    raw = game_id[4:]  # strip 'nba_'
    if len(raw) != 10 or raw[:3] != "004":
        return None
    round_digit = raw[7]
    series_digit = raw[8]
    game_digit = raw[9]
    try:
        return int(round_digit), int(series_digit), int(game_digit)
    except ValueError:
        return None


def process_nba(con, seasons: list[int]) -> None:
    """NBA postseason processing.

    2026+: game_id encodes round/series directly (004YYRSGG format) — write
    round labels from game_id and reconstruct series from that.
    Pre-2026: game_ids use legacy formats; round labels already exist in
    postseason_game_rounds, so reconstruct series and clinchers from there.
    """
    total_round_rows = total_series_rows = total_clinchers = 0

    for season in seasons:
        if season >= 2026:
            # New format: decode round from game_id
            rows = con.execute(
                """
                SELECT game_id, date, team_id, opponent_team_id, result
                FROM team_games
                WHERE league = 'NBA' AND season = ? AND game_type = 'postseason'
                ORDER BY date, game_id
                """,
                [season],
            ).fetchall()

            series_map: dict[tuple, list] = defaultdict(list)
            round_rows: list[tuple] = []
            seen_game_ids: set[str] = set()

            for game_id, game_date, team_id, opp_id, result in rows:
                decoded = decode_nba_game_id(game_id)
                if not decoded:
                    continue
                round_num, series_num, game_num = decoded
                round_name_info = NBA_ROUND_NAMES.get(str(round_num))
                if not round_name_info:
                    continue
                round_order, round_name = round_name_info
                series_map[(round_num, series_num)].append(
                    (game_id, game_date, team_id, opp_id, result, round_order, round_name)
                )
                if game_id not in seen_game_ids:
                    round_rows.append(("NBA", game_id, season, round_order, round_name, "game_id"))
                    seen_game_ids.add(game_id)

            series_rows: list[tuple] = []
            clinch_game_ids: list[str] = []
            clinch_team_ids: list[str] = []

            for (round_num, series_num), game_list in series_map.items():
                round_order, round_name = game_list[0][5], game_list[0][6]
                tid_a = game_list[0][2]
                tid_b = game_list[0][3]
                wins: dict[str, int] = defaultdict(int)
                for game_id, game_date, team_id, opp_id, result, *_ in game_list:
                    if result == "W":
                        wins[team_id] += 1
                unique_game_ids = list({g[0] for g in game_list})
                dates = sorted({g[1] for g in game_list})
                winner_tid = next((t for t, w in wins.items() if w >= NBA_WINS_TO_CLINCH), None)
                series_id = f"nba_{season}_{round_order}_{series_num}"
                series_rows.append((
                    "NBA", season, series_id, tid_a, tid_b,
                    dates[0], dates[-1], len(unique_game_ids),
                    round_order, round_name, "game_id",
                ))
                if winner_tid:
                    winner_win_games = sorted(
                        [g for g in game_list if g[2] == winner_tid and g[4] == "W"],
                        key=lambda g: g[1],
                    )
                    if winner_win_games:
                        clinch_game_ids.append(winner_win_games[-1][0])
                        clinch_team_ids.append(winner_tid)

            r1 = upsert_game_rounds(con, round_rows)
            r2 = upsert_series(con, series_rows)
            r3 = set_series_clinching(con, clinch_game_ids, clinch_team_ids)
        else:
            # Legacy format: reconstruct from existing postseason_game_rounds
            series_rows, _, clinch_game_ids, clinch_team_ids = reconstruct_from_game_rounds(
                con, "NBA", season, "game_rounds"
            )
            r1 = 0
            r2 = upsert_series(con, series_rows)
            r3 = set_series_clinching(con, clinch_game_ids, clinch_team_ids)

        total_round_rows += r1
        total_series_rows += r2
        total_clinchers += r3
        print(f"  NBA {season}: {len(series_rows)} series, {r1} new round labels, {r3} clinchers")

    print(f"NBA total: {total_series_rows} series, {total_round_rows} new round rows, {total_clinchers} clinchers")


# ---------------------------------------------------------------------------
# MLB
# ---------------------------------------------------------------------------

def _season_has_api_game_ids(con, league: str, season: int) -> bool:
    """Return True if this season has any API-format game_ids (mlb_<int> or nba_004...)."""
    if league == "MLB":
        row = con.execute(
            """SELECT COUNT(*) FROM team_games
               WHERE league='MLB' AND season=? AND game_type='postseason'
               AND regexp_matches(game_id, '^mlb_[0-9]+$')""",
            [season],
        ).fetchone()
    else:
        return False
    return (row[0] if row else 0) > 0


def process_mlb(con, seasons: list[int]) -> None:
    """MLB postseason processing.

    Seasons with API game_ids (mlb_<gamePk>): fetch series/round info from Stats API.
    Seasons with Retrosheet game_ids: reconstruct from existing postseason_game_rounds.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "whenwin-ingest/1.0"

    total_round_rows = total_series_rows = total_clinchers = 0

    for season in seasons:
        if not _season_has_api_game_ids(con, "MLB", season):
            # Retrosheet era: reconstruct from existing round labels
            series_rows, _, clinch_game_ids, clinch_team_ids = reconstruct_from_game_rounds(
                con, "MLB", season, "game_rounds"
            )
            r1 = 0
            r2 = upsert_series(con, series_rows)
            r3 = set_series_clinching(con, clinch_game_ids, clinch_team_ids)
            total_round_rows += r1
            total_series_rows += r2
            total_clinchers += r3
            print(f"  MLB {season}: {len(series_rows)} series (reconstructed), {r3} clinchers")
            continue
        game_type_param = "F,D,L,W"
        url = (
            f"https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&season={season}&gameType={game_type_param}"
            f"&hydrate=linescore&fields=dates,games,gamePk,gameType,"
            f"seriesDescription,seriesGameNumber,gamesInSeries,teams,"
            f"team,id,score,isWinner,status,abstractGameState"
        )
        resp = session.get(url, timeout=15)
        time.sleep(0.3)
        if resp.status_code == 404:
            print(f"  MLB {season}: no data (404)")
            continue
        resp.raise_for_status()

        # Resolve game_pks that exist in our DB for this season
        db_games = con.execute(
            """
            SELECT DISTINCT game_id FROM team_games
            WHERE league = 'MLB' AND season = ? AND game_type = 'postseason'
            """,
            [season],
        ).fetchall()
        db_game_pks: set[str] = {r[0] for r in db_games}

        # Group by series_description + team pair to build series entries
        # key: (series_desc, frozenset of team_ids)
        series_map: dict[tuple, list] = defaultdict(list)

        for day in resp.json().get("dates", []):
            for g in day.get("games", []):
                status = g.get("status", {}).get("abstractGameState")
                if status != "Final":
                    continue

                game_pk = g["gamePk"]
                our_game_id = f"mlb_{game_pk}"
                if our_game_id not in db_game_pks:
                    continue  # not in our DB (pre-API era)

                series_desc = g.get("seriesDescription", "")
                series_game_num = g.get("seriesGameNumber", 0)
                games_in_series = g.get("gamesInSeries", 0)

                round_info = MLB_ROUND_NAMES.get(series_desc)
                if not round_info:
                    # Try prefix match for unusual descriptions
                    round_info = next(
                        (v for k, v in MLB_ROUND_NAMES.items() if series_desc.startswith(k)),
                        None,
                    )
                if not round_info:
                    continue
                round_order, round_name = round_info

                away = g["teams"]["away"]
                home = g["teams"]["home"]

                series_map[(series_desc, season, away["team"]["id"], home["team"]["id"])].append({
                    "game_id": our_game_id,
                    "game_pk": game_pk,
                    "round_order": round_order,
                    "round_name": round_name,
                    "series_game_num": series_game_num,
                    "games_in_series": games_in_series,
                    "away_id": away["team"]["id"],
                    "home_id": home["team"]["id"],
                    "away_winner": away.get("isWinner", False),
                    "home_winner": home.get("isWinner", False),
                })

        round_rows: list[tuple] = []
        series_rows_out: list[tuple] = []
        clinch_game_ids: list[str] = []
        clinch_team_ids: list[str] = []

        # Build a mlb_api_team_id -> our team_id lookup for this season
        mlb_id_to_ours = _mlb_api_id_map(con, season)

        for (series_desc, s_season, away_api_id, home_api_id), games in series_map.items():
            games_sorted = sorted(games, key=lambda g: g["series_game_num"])
            round_order = games[0]["round_order"]
            round_name = games[0]["round_name"]

            tid_away = mlb_id_to_ours.get(away_api_id)
            tid_home = mlb_id_to_ours.get(home_api_id)
            if not tid_away or not tid_home:
                continue

            for g in games:
                round_rows.append(("MLB", g["game_id"], season, round_order, round_name, "api"))

            dates_in_series = [  # pull from DB
                con.execute("SELECT date FROM team_games WHERE game_id=? LIMIT 1", [g["game_id"]]).fetchone()[0]
                for g in games
            ]
            series_start = min(dates_in_series)
            series_end = max(dates_in_series)
            max_games = max(g["games_in_series"] for g in games)

            series_id = f"mlb_{season}_{tid_away}_{tid_home}_{round_order}"
            series_rows_out.append((
                "MLB", season, series_id, tid_away, tid_home,
                series_start, series_end, len(games),
                round_order, round_name, "api",
            ))

            # Clincher: last game in the series (series_game_num == games played)
            last_game = games_sorted[-1]
            if last_game["away_winner"]:
                clinch_game_ids.append(last_game["game_id"])
                clinch_team_ids.append(tid_away)
            elif last_game["home_winner"]:
                clinch_game_ids.append(last_game["game_id"])
                clinch_team_ids.append(tid_home)

        r1 = upsert_game_rounds(con, round_rows)
        r2 = upsert_series(con, series_rows_out)
        r3 = set_series_clinching(con, clinch_game_ids, clinch_team_ids)
        total_round_rows += r1
        total_series_rows += r2
        total_clinchers += r3
        print(f"  MLB {season}: {len(series_rows_out)} series, {r1} round labels, {r3} clinchers")

    print(f"MLB total: {total_series_rows} series, {total_round_rows} round rows, {total_clinchers} clinchers")


def _mlb_api_id_map(con, season: int) -> dict[int, str]:
    """Build MLB Stats API team_id -> our team_id for a given season."""
    from ingest_mlb_api import TEAM_ID_MAP
    result = {}
    cache: dict = {}
    from api_utils import resolve_team_id
    for api_id, (city, name) in TEAM_ID_MAP.items():
        try:
            our_id = resolve_team_id(con, "MLB", season, city, name, cache)
            result[api_id] = our_id
        except ValueError:
            pass
    return result


# ---------------------------------------------------------------------------
# NFL
# ---------------------------------------------------------------------------

NFL_TOKEN_MAP = {
    "wc": (1, "Wild Card"),
    "div": (2, "Divisional"),
    "cc": (3, "Conference Championship"),
    "conf": (3, "Conference Championship"),
    "sb": (4, "Super Bowl"),
}


def nfl_round_from_game_id(game_id: str) -> tuple[int, str] | None:
    """Extract (round_order, round_name) from NFL game_id token."""
    parts = game_id.split("_")
    # nfl_<season>_<token>_... — token is parts[2]
    if len(parts) < 3:
        return None
    token = parts[2].lower()
    return NFL_TOKEN_MAP.get(token)


def process_nfl(con, seasons: list[int]) -> None:
    """NFL postseason processing.

    Round labels: only written for new-format game_ids (token-based, ~2013+).
    Old-format game_ids (week-number-based) are already covered in
    postseason_game_rounds from prior manual ingestion — INSERT OR REPLACE
    leaves those rows untouched.

    is_series_clinching: bulk-set for ALL NFL postseason wins across all seasons
    since every game in the single-elimination bracket is a series clincher.
    """
    total_round_rows = 0

    for season in seasons:
        rows = con.execute(
            """
            SELECT DISTINCT game_id, season
            FROM team_games
            WHERE league = 'NFL' AND season = ? AND game_type = 'postseason'
            """,
            [season],
        ).fetchall()

        round_rows: list[tuple] = []
        seen: set[str] = set()

        for game_id, s in rows:
            round_info = nfl_round_from_game_id(game_id)
            if not round_info or game_id in seen:
                continue
            round_order, round_name = round_info
            round_rows.append(("NFL", game_id, s, round_order, round_name, "game_id"))
            seen.add(game_id)

        r1 = upsert_game_rounds(con, round_rows)
        total_round_rows += r1
        if r1:
            print(f"  NFL {season}: {r1} round labels")

    # Single-elimination: every postseason win is a series clincher.
    # Apply across all seasons in one bulk update rather than row by row.
    season_list = seasons
    con.execute(
        """
        UPDATE team_games
        SET is_series_clinching = TRUE
        WHERE league = 'NFL'
          AND game_type = 'postseason'
          AND result = 'W'
          AND season IN (SELECT * FROM UNNEST(?))
        """,
        [season_list],
    )
    total_clinchers = con.execute(
        """
        SELECT COUNT(*) FROM team_games
        WHERE league = 'NFL' AND game_type = 'postseason'
          AND result = 'W' AND is_series_clinching = TRUE
          AND season IN (SELECT * FROM UNNEST(?))
        """,
        [season_list],
    ).fetchone()[0]

    print(f"NFL total: {total_round_rows} new round rows, {total_clinchers} clinchers set")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    con = connect(args.db, args.schema)

    leagues = [args.league] if args.league else ["NHL", "NBA", "MLB", "NFL"]

    for league in leagues:
        seasons = get_postseason_seasons(con, league, args.from_season)
        if not seasons:
            print(f"{league}: no postseason seasons found")
            continue
        season_range = f"{seasons[0]}–{seasons[-1]}"
        print(f"\n{league} ({len(seasons)} seasons, {season_range})")
        if league == "NHL":
            process_nhl(con, seasons)
        elif league == "NBA":
            process_nba(con, seasons)
        elif league == "MLB":
            process_mlb(con, seasons)
        elif league == "NFL":
            process_nfl(con, seasons)

    print("\nDone.")


if __name__ == "__main__":
    main()
