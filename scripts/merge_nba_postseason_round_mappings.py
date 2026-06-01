#!/usr/bin/env python3
"""Merge NBA postseason round mappings into a single, complete CSV.

Inputs:
- raw/nba_postseason_round_labels_1978plus.csv
  (per-game mapping derived from nba_1947_present.csv; may have blank round_name)
- raw/nba_postseason_round_labels_filled_from_series_1978plus.csv
  (per-game mapping derived from a filled series manifest; used to fill blanks)

Output:
- raw/nba_postseason_round_labels_1978plus_merged.csv

No DB modifications.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


ROUND_ORDER_BY_NAME = {
    "Play-In": 0,
    "First Round": 1,
    "Conf. Semifinals": 2,
    "Conf. Finals": 3,
    "NBA Finals": 4,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="raw/nba_postseason_round_labels_1978plus.csv")
    p.add_argument("--fill", default="raw/nba_postseason_round_labels_filled_from_series_1978plus.csv")
    p.add_argument("--out", default="raw/nba_postseason_round_labels_1978plus_merged.csv")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    fill_by_game_id: dict[str, tuple[int, str]] = {}
    with Path(args.fill).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            gid = (row.get("game_id") or "").strip()
            if not gid:
                continue
            ro = int(row["round_order"])
            rn = (row.get("round_name") or "").strip()
            fill_by_game_id[gid] = (ro, rn)

    out_rows: list[dict[str, str]] = []
    base_total = 0
    filled = 0
    still_blank = 0

    with Path(args.base).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            base_total += 1
            gid = row["game_id"].strip()
            season = row["season"].strip()
            dt = row["date"].strip()
            rn = (row.get("round_name") or "").strip()
            src_type = (row.get("source_gameType") or "").strip()
            src_sublabel = (row.get("source_gameSubLabel") or "").strip()

            round_order = ""
            source = "kaggle_nba_1947_present"

            if not rn and gid in fill_by_game_id:
                ro, rn2 = fill_by_game_id[gid]
                rn = rn2
                round_order = str(ro)
                filled += 1
                source = "series_manifest_fill"
            else:
                if rn in ROUND_ORDER_BY_NAME:
                    round_order = str(ROUND_ORDER_BY_NAME[rn])

            if not rn:
                still_blank += 1

            out_rows.append(
                {
                    "league": "NBA",
                    "season": season,
                    "date": dt,
                    "game_id": gid,
                    "round_order": round_order,
                    "round_name": rn,
                    "source": source,
                    "source_gameType": src_type,
                    "source_gameSubLabel": src_sublabel,
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "league",
                "season",
                "date",
                "game_id",
                "round_order",
                "round_name",
                "source",
                "source_gameType",
                "source_gameSubLabel",
            ],
        )
        w.writeheader()
        for row in sorted(out_rows, key=lambda r: (int(r["season"]), r["date"], r["game_id"])):
            w.writerow(row)

    print(f"base_rows: {base_total}")
    print(f"filled_from_series_manifest: {filled}")
    print(f"still_blank_round_name: {still_blank}")
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()

