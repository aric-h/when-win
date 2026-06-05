#!/usr/bin/env python3
"""Export NHL postseason game round labels by joining `team_games` to a series manifest.

Input:
- NHL series manifest CSV with columns:
  league, season, series_id, team_id_a, team_id_b, series_start_date, series_end_date,
  games_in_matchup, round_order, round_name

Output:
- CSV mapping (league, season, game_id) -> (series_id, round_order, round_name)

No DB modifications.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class SeriesRow:
    season: int
    series_id: str
    team_a: str
    team_b: str
    start: date
    end: date
    games_in_matchup: int
    round_order: int
    round_name: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument(
        "--manifest",
        default="raw/nhl playoffs - nhl_postseason_series_manifest_draft_1978plus.csv",
        help="Filled series manifest CSV",
    )
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--out", default="raw/nhl_postseason_round_labels_1978plus.csv")
    p.add_argument("--unmatched-out", default="raw/nhl_postseason_round_labels_unmatched.csv")
    p.add_argument("--ambiguous-out", default="raw/nhl_postseason_round_labels_ambiguous.csv")
    return p.parse_args()


def parse_iso_date(value: str) -> date:
    y, m, d = value.split("-")
    return date(int(y), int(m), int(d))


def load_manifest(path: Path) -> list[SeriesRow]:
    rows: list[SeriesRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        required = {
            "season",
            "series_id",
            "team_id_a",
            "team_id_b",
            "series_start_date",
            "series_end_date",
            "games_in_matchup",
            "round_order",
            "round_name",
        }
        missing = required - set(r.fieldnames or [])
        if missing:
            raise SystemExit(f"Manifest missing columns: {sorted(missing)}")

        for row in r:
            league = (row.get("league") or "").strip().upper()
            if league and league != "NHL":
                continue
            season = int(row["season"])
            rows.append(
                SeriesRow(
                    season=season,
                    series_id=row["series_id"].strip(),
                    team_a=row["team_id_a"].strip(),
                    team_b=row["team_id_b"].strip(),
                    start=parse_iso_date(row["series_start_date"].strip()),
                    end=parse_iso_date(row["series_end_date"].strip()),
                    games_in_matchup=int(row["games_in_matchup"]),
                    round_order=int(row["round_order"]),
                    round_name=row["round_name"].strip(),
                )
            )
    return rows


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)
    manifest = load_manifest(Path(args.manifest))

    # Index manifest by (season, unordered team pair)
    by_pair: dict[tuple[int, str, str], list[SeriesRow]] = {}
    for s in manifest:
        a, b = sorted([s.team_a, s.team_b])
        by_pair.setdefault((s.season, a, b), []).append(s)

    # Pull distinct NHL postseason games from DB
    db_rows = con.execute(
        """
        WITH g AS (
          SELECT DISTINCT
            season,
            game_id,
            date,
            CASE WHEN team_id < opponent_team_id THEN team_id ELSE opponent_team_id END AS team_a,
            CASE WHEN team_id < opponent_team_id THEN opponent_team_id ELSE team_id END AS team_b
          FROM team_games
          WHERE league='NHL'
            AND game_type='postseason'
            AND season BETWEEN ? AND ?
            AND date IS NOT NULL
        )
        SELECT season, game_id, date, team_a, team_b
        FROM g
        ORDER BY season, date, game_id
        """,
        [args.from_season, args.to_season],
    ).fetchall()

    matched: list[tuple] = []
    unmatched: list[tuple] = []
    ambiguous: list[tuple] = []

    for season, game_id, d, team_a, team_b in db_rows:
        season_i = int(season)
        gid = str(game_id)
        game_date: date = d
        a = str(team_a)
        b = str(team_b)
        candidates = by_pair.get((season_i, a, b), [])
        # Filter by date window
        window = [s for s in candidates if s.start <= game_date <= s.end]
        if not window:
            unmatched.append((gid, season_i, game_date.isoformat(), a, b))
            continue
        if len(window) > 1:
            # Choose the tightest window as best guess; still log ambiguity.
            window_sorted = sorted(window, key=lambda s: ((s.end - s.start).days, s.series_id))
            chosen = window_sorted[0]
            ambiguous.append((gid, season_i, game_date.isoformat(), a, b, ";".join(s.series_id for s in window_sorted)))
        else:
            chosen = window[0]

        matched.append(
            (
                "NHL",
                season_i,
                gid,
                chosen.series_id,
                chosen.round_order,
                chosen.round_name,
                game_date.isoformat(),
                a,
                b,
            )
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "league",
                "season",
                "game_id",
                "series_id",
                "round_order",
                "round_name",
                "date",
                "team_id_a",
                "team_id_b",
            ]
        )
        for row in matched:
            w.writerow(list(row))

    un_path = Path(args.unmatched_out)
    with un_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date", "team_id_a", "team_id_b"])
        for row in unmatched:
            w.writerow(list(row))

    amb_path = Path(args.ambiguous_out)
    with amb_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date", "team_id_a", "team_id_b", "candidate_series_ids"])
        for row in ambiguous:
            w.writerow(list(row))

    print(f"db_postseason_games: {len(db_rows)}")
    print(f"matched_games: {len(matched)}")
    print(f"unmatched_games: {len(unmatched)}")
    print(f"ambiguous_games: {len(ambiguous)}")
    print(f"wrote: {out_path}")
    print(f"wrote: {un_path}")
    print(f"wrote: {amb_path}")


if __name__ == "__main__":
    main()

