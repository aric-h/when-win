#!/usr/bin/env python3
"""Export NBA postseason game round labels by joining `team_games` to a series manifest.

Use this after filling round_order/round_name at the series level for the subset
of NBA postseason games missing round labels in nba_postseason_round_labels_1978plus.csv.

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
    round_order: int
    round_name: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--manifest", default="raw/nba_postseason_series_manifest_draft_missing_rounds_filled.csv")
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=9999)
    p.add_argument("--out", default="raw/nba_postseason_round_labels_filled_from_series_1978plus.csv")
    return p.parse_args()


def parse_iso_date(value: str) -> date:
    y, m, d = value.split("-")
    return date(int(y), int(m), int(d))


def load_manifest(path: Path) -> list[SeriesRow]:
    rows: list[SeriesRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            league = (row.get("league") or "").strip().upper()
            if league and league != "NBA":
                continue
            rows.append(
                SeriesRow(
                    season=int(row["season"]),
                    series_id=row["series_id"].strip(),
                    team_a=row["team_id_a"].strip(),
                    team_b=row["team_id_b"].strip(),
                    start=parse_iso_date(row["series_start_date"].strip()),
                    end=parse_iso_date(row["series_end_date"].strip()),
                    round_order=int(row["round_order"]),
                    round_name=row["round_name"].strip(),
                )
            )
    return rows


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)
    manifest = load_manifest(Path(args.manifest))

    # Index by (season, unordered team pair)
    by_pair: dict[tuple[int, str, str], list[SeriesRow]] = {}
    for s in manifest:
        a, b = sorted([s.team_a, s.team_b])
        by_pair.setdefault((s.season, a, b), []).append(s)

    # Fetch distinct NBA postseason games for seasons included (we'll filter by manifest date windows)
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
          WHERE league='NBA'
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

    out_rows: list[tuple] = []
    matched = 0
    for season, game_id, d, team_a, team_b in db_rows:
        season_i = int(season)
        gid = str(game_id)
        game_date: date = d
        a = str(team_a)
        b = str(team_b)
        candidates = by_pair.get((season_i, a, b), [])
        if not candidates:
            continue
        window = [s for s in candidates if s.start <= game_date <= s.end]
        if not window:
            continue
        chosen = sorted(window, key=lambda s: ((s.end - s.start).days, s.series_id))[0]
        out_rows.append(("NBA", season_i, gid, chosen.series_id, chosen.round_order, chosen.round_name))
        matched += 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["league", "season", "game_id", "series_id", "round_order", "round_name"])
        for row in sorted(out_rows, key=lambda r: (r[1], r[2], r[0])):
            w.writerow(list(row))

    print(f"manifest_series: {len(manifest)}")
    print(f"matched_games_from_manifest: {matched}")
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()

