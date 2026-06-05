#!/usr/bin/env python3
"""Export NBA postseason round labels by matching nba_1947_present.csv to team_games.game_id.

This does NOT modify the DB. It produces a CSV mapping suitable for later insertion
into a round-mapping table.

Source:
- raw/nba/kaggle/nba_1947_present.csv

DB:
- team_games rows where league='NBA' and game_type='postseason'

We use:
- game_id = 'nba_' + csv.gameId
- round label = csv.gameLabel (may be blank for some historical rows)
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--csv", default="raw/nba/kaggle/nba_1947_present.csv")
    p.add_argument("--from-season", type=int, default=1978, help="DB season (start year) lower bound")
    p.add_argument("--to-season", type=int, default=9999, help="DB season (start year) upper bound")
    p.add_argument("--out", default="raw/nba_postseason_round_labels_1978plus.csv")
    p.add_argument("--unmatched-out", default="raw/nba_postseason_round_labels_unmatched.csv")
    return p.parse_args()

def normalize_round_name(round_name: str, source_game_type: str) -> str:
    rn = (round_name or "").strip()
    gt = (source_game_type or "").strip()
    if gt == "Play-in Tournament":
        return "Play-In"
    if rn in {"SoFi Play-In Tournament", "East Play-In", "West Play-In"}:
        return "Play-In"
    if rn in {"NBA Finals"}:
        return "NBA Finals"
    if rn in {"East - First Round", "West - First Round", "East First Round", "West First Round"}:
        return "First Round"
    if rn in {"East - Conf. Semifinals", "West - Conf. Semifinals", "East Conf. Semifinals", "West Conf. Semifinals"}:
        return "Conf. Semifinals"
    if rn in {"East - Conf. Finals", "West - Conf. Finals", "East Conf. Finals", "West Conf. Finals"}:
        return "Conf. Finals"
    return rn


def main() -> None:
    args = parse_args()
    con = duckdb.connect(str(Path(args.db)), read_only=True)

    team_api_id_by_team_id = {
        team_id: int(fid.split("nba_franchise_", 1)[1])
        for team_id, fid in con.execute(
            "SELECT team_id, franchise_id FROM teams WHERE league='NBA' AND franchise_id LIKE 'nba_franchise_%'"
        ).fetchall()
    }

    existing_rows = con.execute(
        """
        SELECT DISTINCT game_id, season, date
        FROM team_games
        WHERE league='NBA'
          AND game_type='postseason'
          AND season BETWEEN ? AND ?
        """,
        [args.from_season, args.to_season],
    ).fetchall()
    existing = {game_id: (season, date) for game_id, season, date in existing_rows}

    # Build mapping from source CSV. Keep first occurrence per gameId.
    label_by_game_id: dict[str, str] = {}
    meta_by_game_id: dict[str, tuple[str, str]] = {}
    # (gameType, gameSubLabel)
    game_id_by_key: dict[tuple[str, int, int], str] = {}
    # (date, home_api_id, away_api_id) -> game_id
    with Path(args.csv).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            raw_id = (row.get("gameId") or "").strip()
            if not raw_id:
                continue
            game_id = f"nba_{raw_id}"
            if game_id in label_by_game_id:
                continue
            label = (row.get("gameLabel") or "").strip()
            label_by_game_id[game_id] = label
            meta_by_game_id[game_id] = ((row.get("gameType") or "").strip(), (row.get("gameSubLabel") or "").strip())

            # Keyed lookup for matching non-numeric DB game_ids (e.g. basketball-reference imports).
            dt = (row.get("gameDateTimeEst") or "").strip()
            date = dt.split(" ", 1)[0] if dt else ""
            try:
                home_api_id = int((row.get("hometeamId") or "").strip() or "0")
                away_api_id = int((row.get("awayteamId") or "").strip() or "0")
            except ValueError:
                continue
            if date and home_api_id and away_api_id:
                game_id_by_key.setdefault((date, home_api_id, away_api_id), game_id)

    matched: list[tuple[str, int, str, str, str, str]] = []
    # game_id, season, date, round_name, game_type_src, game_sublabel_src
    unmatched: list[tuple[str, int, str]] = []
    blank_rounds = 0

    for game_id, (season, date) in existing.items():
        resolved_game_id = game_id
        if resolved_game_id not in label_by_game_id:
            # Try to match basketball-reference style IDs:
            # nba_{season}_br_{YYYY-MM-DD}_nba_<away_team_id>_at_nba_<home_team_id>
            m = re.match(r"^nba_\d+_br_(\d{4}-\d{2}-\d{2})_(nba_.+?)_at_(nba_.+)$", resolved_game_id)
            if m:
                game_date, away_team_id, home_team_id = m.group(1), m.group(2), m.group(3)
                away_api_id = team_api_id_by_team_id.get(away_team_id)
                home_api_id = team_api_id_by_team_id.get(home_team_id)
                if away_api_id and home_api_id:
                    resolved_game_id = game_id_by_key.get((game_date, home_api_id, away_api_id), game_id)

        if resolved_game_id not in label_by_game_id:
            unmatched.append((game_id, season, str(date)))
            continue

        round_name = label_by_game_id[resolved_game_id]
        if not round_name:
            blank_rounds += 1
        src_type, src_sublabel = meta_by_game_id.get(resolved_game_id, ("", ""))
        round_name = normalize_round_name(round_name, src_type)
        matched.append((game_id, season, str(date), round_name, src_type, src_sublabel))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date", "round_name", "source_gameType", "source_gameSubLabel"])
        for row in sorted(matched, key=lambda r: (r[1], r[2], r[0])):
            w.writerow(list(row))

    unmatched_path = Path(args.unmatched_out)
    with unmatched_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game_id", "season", "date"])
        for row in sorted(unmatched, key=lambda r: (r[1], r[2], r[0])):
            w.writerow(list(row))

    round_counts = Counter(r[3] for r in matched)
    print(f"db_postseason_game_ids: {len(existing)}")
    print(f"matched_game_ids: {len(matched)}")
    print(f"unmatched_game_ids: {len(unmatched)}")
    print(f"blank_round_name: {blank_rounds}")
    print("top_round_names:", round_counts.most_common(12))
    print(f"wrote: {out_path}")
    print(f"wrote: {unmatched_path}")


if __name__ == "__main__":
    main()
