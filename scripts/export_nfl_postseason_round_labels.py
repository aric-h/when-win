#!/usr/bin/env python3
"""Export NFL postseason round labels for 1978+.

This does NOT modify the DB. It produces a CSV mapping suitable for later insertion
into a round-mapping table.

Sources:
- team_games (authoritative list of games present in the DB)
- raw/nfl/pro-football-reference/<season>.csv (optional; used to validate/override when present)

Round logic:
1) If DB game_id contains explicit tokens (_sb_, _cc_, _div_, _wc_), use that.
2) Else infer by sorting games within a season by date:
   - last game: Super Bowl
   - previous 2: Conference Championship
   - previous 4: Divisional
   - remaining: Wild Card (or other early rounds; we label as Wild Card for now)

The inference works without a separate playoff-format reference because it only relies on
the structural invariants of the NFL bracket: 1 SB, 2 CC, 4 DIV, rest are earlier.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import duckdb


PFR_ROUND = {
    "wildcard": "Wild Card",
    "division": "Divisional",
    "confchamp": "Conference Championship",
    "superbowl": "Super Bowl",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--pfr-dir", default="raw/nfl/pro-football-reference")
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--out", default="raw/nfl_postseason_round_labels_1978plus.csv")
    p.add_argument("--conflicts-out", default="raw/nfl_postseason_round_labels_conflicts.csv")
    return p.parse_args()


def norm_spaces(value: str) -> str:
    return " ".join(value.strip().split())


def load_team_name_index(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[str, int, int | None]]]:
    rows = con.execute(
        """
        SELECT team_id, city, team_name, start_year, end_year
        FROM teams
        WHERE league='NFL'
        """
    ).fetchall()
    out: dict[str, list[tuple[str, int, int | None]]] = {}
    for team_id, city, team_name, start_year, end_year in rows:
        full = norm_spaces(f"{city} {team_name}")
        out.setdefault(full, []).append((str(team_id), int(start_year), int(end_year) if end_year is not None else None))
    return out


def resolve_team_id(full_name: str, season: int, index: dict[str, list[tuple[str, int, int | None]]]) -> str:
    key = norm_spaces(full_name)
    eras = index.get(key)
    if not eras:
        raise KeyError(key)
    matches = [e for e in eras if e[1] <= season and (e[2] is None or e[2] >= season)]
    if not matches:
        raise KeyError(f"{key} (no era for season {season})")
    if len(matches) > 1:
        raise KeyError(f"{key} (ambiguous for season {season})")
    return matches[0][0]


def parse_explicit_round_from_game_id(game_id: str) -> str | None:
    m = re.match(r"^nfl_\d+_(wc|div|cc|sb)_", game_id)
    if not m:
        return None
    token = m.group(1)
    return {
        "wc": "Wild Card",
        "div": "Divisional",
        "cc": "Conference Championship",
        "sb": "Super Bowl",
    }[token]


def infer_rounds_for_season(games: list[tuple[str, str]]) -> dict[str, str]:
    """games: list[(game_id, date_iso)] distinct games for a season."""
    games_sorted = sorted(games, key=lambda r: (r[1], r[0]))
    n = len(games_sorted)
    out: dict[str, str] = {}
    if n == 0:
        return out
    # Work backwards
    out[games_sorted[-1][0]] = "Super Bowl"
    for gid, _ in games_sorted[max(0, n - 3) : n - 1]:
        out[gid] = "Conference Championship"
    for gid, _ in games_sorted[max(0, n - 7) : max(0, n - 3)]:
        out[gid] = "Divisional"
    for gid, _ in games_sorted[: max(0, n - 7)]:
        out[gid] = "Wild Card"
    return out


def load_pfr_rounds(
    pfr_dir: Path,
    seasons: set[int],
    team_index: dict[str, list[tuple[str, int, int | None]]],
) -> dict[tuple[int, str, str, str], str]:
    """Return mapping (season, date, away_team_id, home_team_id) -> round_name."""
    out: dict[tuple[int, str, str, str], str] = {}
    for season in sorted(seasons):
        path = pfr_dir / f"{season}.csv"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            _header = next(r, None)
            for row in r:
                if not row or len(row) < 10:
                    continue
                raw_week = row[0].strip()
                if raw_week.isdigit():
                    continue
                round_name = PFR_ROUND.get(raw_week.strip().lower())
                if not round_name:
                    continue
                date = row[2].strip()
                winner = row[4].strip()
                marker = row[5].strip()
                loser = row[6].strip()
                try:
                    winner_id = resolve_team_id(winner, season, team_index)
                    loser_id = resolve_team_id(loser, season, team_index)
                except KeyError:
                    continue
                if marker == "@":
                    away_id, home_id = winner_id, loser_id
                else:
                    away_id, home_id = loser_id, winner_id
                out[(season, date, away_id, home_id)] = round_name
    return out


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)

    # Distinct games for NFL postseason in DB
    games = con.execute(
        """
        SELECT DISTINCT game_id, season, date, team_id, opponent_team_id
        FROM team_games
        WHERE league='NFL'
          AND game_type='postseason'
          AND season BETWEEN ? AND ?
        """,
        [args.from_season, args.to_season],
    ).fetchall()

    # Build per game_id canonical (season, date, away_id, home_id) using a stable ordering.
    # We don't know away/home from team_games rows; infer from game_id when possible, else
    # fall back to sorted pair for matching.
    canonical: dict[str, tuple[int, str, str, str]] = {}
    for game_id, season, date, team_id, opp_id in games:
        gid = str(game_id)
        if gid in canonical:
            continue
        season_i = int(season)
        date_s = str(date)
        # Try to parse *_at_* or github format to get away/home.
        m_at = re.search(r"_(?P<away>nfl_[a-z0-9_]+)_at_(?P<home>nfl_[a-z0-9_]+)$", gid)
        if m_at:
            away_id = m_at.group("away")
            home_id = m_at.group("home")
        else:
            # fallback: use lexicographic order; PFR matching will fail for these but inference still works
            a, b = sorted([str(team_id), str(opp_id)])
            away_id, home_id = a, b
        canonical[gid] = (season_i, date_s, away_id, home_id)

    seasons_in_db = {season for season, *_ in canonical.values()}
    team_index = load_team_name_index(con)
    pfr_map = load_pfr_rounds(Path(args.pfr_dir), seasons_in_db, team_index)

    # Group games by season for inference
    by_season: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for gid, (season, date, _away, _home) in canonical.items():
        by_season[season].append((gid, date))

    inferred_by_season = {season: infer_rounds_for_season(glist) for season, glist in by_season.items()}

    out_rows: list[tuple] = []
    conflicts: list[tuple] = []
    for gid, (season, date, away_id, home_id) in canonical.items():
        explicit = parse_explicit_round_from_game_id(gid)
        inferred = inferred_by_season.get(season, {}).get(gid)
        pfr_round = pfr_map.get((season, date, away_id, home_id))

        round_name = explicit or pfr_round or inferred or ""
        method = "explicit_token" if explicit else ("pfr" if pfr_round else ("inferred_by_order" if inferred else ""))

        if explicit and pfr_round and explicit != pfr_round:
            conflicts.append((gid, season, date, explicit, pfr_round, away_id, home_id))

        out_rows.append((gid, season, date, round_name, method, pfr_round or ""))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date", "round_name", "method", "pfr_round_name"])
        for row in sorted(out_rows, key=lambda r: (r[1], r[2], r[0])):
            w.writerow(list(row))

    conflicts_path = Path(args.conflicts_out)
    with conflicts_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date", "explicit_round", "pfr_round", "away_team_id", "home_team_id"])
        for row in sorted(conflicts, key=lambda r: (r[1], r[2], r[0])):
            w.writerow(list(row))

    print(f"db_postseason_game_ids: {len(canonical)}")
    print(f"pfr_matched_games: {sum(1 for r in out_rows if r[5])}")
    print(f"conflicts: {len(conflicts)}")
    print(f"wrote: {out_path}")
    print(f"wrote: {conflicts_path}")


if __name__ == "__main__":
    main()

