#!/usr/bin/env python3
"""Update MLB team_games with final scores from a Baseball-Reference *text/markdown* dump.

Input format example:
  Wednesday, April 15, 2026
  Texas Rangers (5) @ Athletics (6)     Boxscore

This updater matches games against existing MLB schedule rows already in `team_games`
by (date, away_team_id, home_team_id). It then fills:
- result (W/L)
- pts_for / pts_against

Limitations:
- If the same teams play a doubleheader on the same date, and the dump does not
  include game numbers, the match can be ambiguous. Ambiguous rows are skipped
  and written to an output CSV for manual handling.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb


MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


TEAM_ALIASES = {
    "Arizona D'Backs": "Arizona Diamondbacks",
    "D'Backs": "Arizona Diamondbacks",
    "White Sox": "Chicago White Sox",
    "Red Sox": "Boston Red Sox",
}


@dataclass(frozen=True)
class ParsedGame:
    game_date: str  # YYYY-MM-DD
    away_name: str
    away_score: int
    home_name: str
    home_score: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--md", default="raw/mlb/baseball_reference/2026_05_08.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--unmatched-out", default="raw/mlb/baseball_reference/score_update_unmatched.csv")
    p.add_argument("--ambiguous-out", default="raw/mlb/baseball_reference/score_update_ambiguous.csv")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def canon_team_name(name: str) -> str:
    v = " ".join(name.strip().split())
    v = TEAM_ALIASES.get(v, v)
    return v


def parse_md(path: Path) -> list[ParsedGame]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    date_re = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([A-Za-z]+) (\d{1,2}), (\d{4})$")
    game_re = re.compile(r"^(.+?) \((\d+)\) @ (.+?) \((\d+)\)\s+Boxscore\s*$")

    current: str | None = None
    out: list[ParsedGame] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = date_re.match(line)
        if m:
            month = MONTHS.get(m.group(2))
            if not month:
                continue
            d = date(int(m.group(4)), month, int(m.group(3)))
            current = d.isoformat()
            continue

        m = game_re.match(line)
        if m and current:
            away = canon_team_name(m.group(1))
            away_score = int(m.group(2))
            home = canon_team_name(m.group(3))
            home_score = int(m.group(4))
            out.append(ParsedGame(current, away, away_score, home, home_score))

    return out


def build_team_index(con: duckdb.DuckDBPyConnection, season: int) -> dict[str, str]:
    rows = con.execute(
        """
        SELECT team_id, city, team_name
        FROM teams
        WHERE league='MLB'
          AND start_year <= ?
          AND (end_year IS NULL OR end_year >= ?)
        """,
        [season, season],
    ).fetchall()
    idx: dict[str, str] = {}
    # full city + name
    for team_id, city, team_name in rows:
        full = f"{city} {team_name}".strip()
        idx[full] = str(team_id)
        idx[team_name] = str(team_id)  # allow mascot-only matches when unique (e.g. Athletics)
    return idx


def resolve_team_id(team_label: str, index: dict[str, str]) -> str | None:
    label = canon_team_name(team_label)
    return index.get(label)


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    parsed = parse_md(Path(args.md))
    if not parsed:
        raise SystemExit(f"No games parsed from {args.md}")

    idx = build_team_index(con, args.season)

    unmatched: list[tuple] = []
    ambiguous: list[tuple] = []
    updates: list[tuple] = []
    # (game_id, team_id, opponent_team_id, result, pts_for, pts_against)

    for g in parsed:
        away_id = resolve_team_id(g.away_name, idx)
        home_id = resolve_team_id(g.home_name, idx)
        if not away_id or not home_id:
            unmatched.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id or "", home_id or ""))
            continue

        candidates = con.execute(
            """
            SELECT DISTINCT game_id
            FROM team_games
            WHERE league='MLB'
              AND game_type='regular'
              AND season=?
              AND date=?
              AND team_id=?
              AND opponent_team_id=?
            """,
            [args.season, g.game_date, away_id, home_id],
        ).fetchall()

        if not candidates:
            # Fallback: handle make-up games where the scheduled row exists on a different date.
            # If we find exactly one unscored scheduled game for this matchup, migrate it to the
            # actual played date and then apply the score update.
            alt = con.execute(
                """
                SELECT DISTINCT game_id, date
                FROM team_games
                WHERE league='MLB'
                  AND game_type='regular'
                  AND season=?
                  AND team_id=?
                  AND opponent_team_id=?
                  AND (pts_for IS NULL OR pts_against IS NULL)
                """,
                [args.season, away_id, home_id],
            ).fetchall()
            if not alt:
                unmatched.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id, home_id))
                continue
            # Pick the closest prior scheduled game for this matchup (common for makeups).
            target_dt = datetime.strptime(g.game_date, "%Y-%m-%d").date()
            parsed_alt = []
            for ogid, od in alt:
                try:
                    odt = datetime.strptime(str(od), "%Y-%m-%d").date()
                except ValueError:
                    continue
                parsed_alt.append((odt, str(ogid)))
            prior = [x for x in parsed_alt if x[0] <= target_dt]
            if not prior:
                unmatched.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id, home_id))
                continue
            odt, old_game_id = max(prior, key=lambda x: x[0])
            if (target_dt - odt).days > 10:
                unmatched.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id, home_id))
                continue
            parts = str(old_game_id).split("_")
            # Expect: mlb_{season}_{date}_{VIS}_{HOME}_{v_game}_{h_game}
            if len(parts) < 7:
                unmatched.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id, home_id))
                continue

            season_token = parts[1]
            vis_code = parts[3]
            home_code = parts[4]
            v_game = parts[5]
            h_game = parts[6]
            new_game_id = f"mlb_{season_token}_{g.game_date}_{vis_code}_{home_code}_{v_game}_{h_game}"

            if not args.dry_run:
                con.execute("BEGIN TRANSACTION")
                try:
                    con.execute(
                        """
                        UPDATE team_games
                        SET game_id=?, date=?
                        WHERE league='MLB' AND game_type='regular' AND game_id=?
                        """,
                        [new_game_id, g.game_date, old_game_id],
                    )
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise

            candidates = [(new_game_id,)]
        if len(candidates) > 1:
            ambiguous.append((g.game_date, g.away_name, g.away_score, g.home_name, g.home_score, away_id, home_id, ";".join(c[0] for c in candidates)))
            continue

        game_id = candidates[0][0]
        if g.away_score > g.home_score:
            away_res, home_res = "W", "L"
        elif g.away_score < g.home_score:
            away_res, home_res = "L", "W"
        else:
            away_res = home_res = "T"

        updates.append((game_id, away_id, home_id, away_res, g.away_score, g.home_score))
        updates.append((game_id, home_id, away_id, home_res, g.home_score, g.away_score))

    if unmatched:
        out_path = Path(args.unmatched_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "away", "away_score", "home", "home_score", "away_team_id", "home_team_id"])
            for row in unmatched:
                w.writerow(list(row))

    if ambiguous:
        amb_path = Path(args.ambiguous_out)
        amb_path.parent.mkdir(parents=True, exist_ok=True)
        with amb_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "away", "away_score", "home", "home_score", "away_team_id", "home_team_id", "candidate_game_ids"])
            for row in ambiguous:
                w.writerow(list(row))

    if args.dry_run:
        print(f"parsed_games: {len(parsed)}")
        print(f"updates(team-rows): {len(updates)}")
        print(f"unmatched: {len(unmatched)}")
        print(f"ambiguous: {len(ambiguous)}")
        return

    con.executemany(
        """
        UPDATE team_games
        SET result=?, pts_for=?, pts_against=?
        WHERE game_id=? AND team_id=? AND opponent_team_id=? AND league='MLB' AND game_type='regular'
        """,
        [(u[3], u[4], u[5], u[0], u[1], u[2]) for u in updates],
    )

    print(f"parsed_games: {len(parsed)}")
    print(f"updated_team_rows: {len(updates)}")
    print(f"unmatched: {len(unmatched)}")
    print(f"ambiguous: {len(ambiguous)}")

    # Report latest scored date after update.
    last = con.execute(
        """
        SELECT MAX(date)
        FROM team_games
        WHERE league='MLB' AND pts_for IS NOT NULL AND pts_against IS NOT NULL
        """,
    ).fetchone()[0]
    print(f"last_mlb_scored_date: {last}")


if __name__ == "__main__":
    main()
