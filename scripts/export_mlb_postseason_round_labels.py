#!/usr/bin/env python3
"""Export MLB postseason round labels by matching Retrosheet game logs to team_games.game_id.

We already ingest the Retrosheet postseason game logs into `team_games` using a deterministic
`game_id` format. This script re-derives that `game_id` per Retrosheet row and emits a CSV
mapping from `game_id` -> `round`.

Rounds are inferred from the source filename:
- gldv.txt -> Division Series (ALDS/NLDS)
- gllc.txt -> League Championship Series (ALCS/NLCS)
- glws.txt -> World Series
- glwc.txt -> Wild Card (game or series depending on year; still labeled "Wild Card")
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb


ROUND_BY_FILE = {
    "gldv.txt": "Division Series (ALDS/NLDS)",
    "gllc.txt": "League Championship Series (LCS)",
    "glws.txt": "World Series",
    "glwc.txt": "Wild Card",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--dir", default="raw/mlb/retrosheet/game_logs/postseason")
    p.add_argument("--from-year", type=int, default=1978)
    p.add_argument("--to-year", type=int, default=2025)
    p.add_argument("--out", default="raw/mlb_postseason_round_labels_1978plus.csv")
    p.add_argument("--unmatched-out", default="raw/mlb_postseason_round_labels_unmatched.csv")
    return p.parse_args()


def yyyymmdd_to_date(value: str) -> str:
    v = value.strip().strip('"')
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"


def main() -> None:
    args = parse_args()
    logs_dir = Path(args.dir)
    con = duckdb.connect(str(Path(args.db)), read_only=True)

    rows: list[tuple[str, str, str, str, str]] = []
    # (game_id, round_name, date, visitor_code, home_code)

    for filename, round_name in ROUND_BY_FILE.items():
        path = logs_dir / filename
        if not path.exists():
            raise SystemExit(f"missing file: {path}")

        with path.open("r", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            for row in r:
                if not row or len(row) < 11:
                    continue

                date_yyyymmdd = row[0].strip().strip('"')
                if not date_yyyymmdd or len(date_yyyymmdd) != 8:
                    continue
                season = int(date_yyyymmdd[0:4])
                if season < args.from_year or season > args.to_year:
                    continue

                date = yyyymmdd_to_date(date_yyyymmdd)
                visitor = row[3].strip().strip('"').upper()
                home = row[6].strip().strip('"').upper()
                v_game = row[5].strip()
                h_game = row[8].strip()
                game_id = f"mlb_{season}_{date}_{visitor}_{home}_{v_game}_{h_game}"
                rows.append((game_id, round_name, date, visitor, home))

    # Deduplicate in case a file has accidental duplicates
    mapping: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for game_id, round_name, *_ in rows:
        prior = mapping.get(game_id)
        if prior and prior != round_name:
            collisions.append((game_id, prior, round_name))
        mapping[game_id] = round_name
    if collisions:
        raise SystemExit(f"round collisions for same game_id: {collisions[:5]} (and {len(collisions)} total)")

    existing = {
        row[0]
        for row in con.execute(
            """
            SELECT DISTINCT game_id
            FROM team_games
            WHERE league='MLB' AND game_type='postseason'
              AND season BETWEEN ? AND ?
            """,
            [args.from_year, args.to_year],
        ).fetchall()
    }

    matched: list[tuple[str, str]] = []
    unmatched: list[tuple[str, str, str, str]] = []
    for game_id, round_name, date, visitor, home in rows:
        if game_id in existing:
            matched.append((game_id, round_name))
        else:
            unmatched.append((game_id, round_name, date, f"{visitor}@{home}"))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "round_name"])
        for game_id, round_name in sorted(set(matched)):
            w.writerow([game_id, round_name])

    unmatched_path = Path(args.unmatched_out)
    with unmatched_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "round_name", "date", "matchup"])
        for row in sorted(set(unmatched)):
            w.writerow(list(row))

    print(f"matched game_ids: {len(set(matched))}")
    print(f"unmatched game_ids: {len(set(unmatched))}")
    print(f"wrote: {out_path}")
    print(f"wrote: {unmatched_path}")


if __name__ == "__main__":
    main()

