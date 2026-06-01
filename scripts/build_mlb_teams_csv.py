#!/usr/bin/env python3
"""Build a minimal MLB team identity CSV from Baseball-Reference active franchises export.

Input:
- raw/mlb/baseball_reference_franchises.csv (copied table; includes note rows)

Output:
- raw/mlb/mlb_teams.csv

Columns (compatible with scripts/import_teams_csv.py; extra cols are ignored):
- league,location,team_name,from,to,franchise_id,retrosheet_code

Notes:
- Only emits the numbered franchise rows (Rk is an integer).
- Converts `to` to NULL if it matches the max Retrosheet schedule year present in raw/mlb/retrosheet.
- Adds a best-effort `retrosheet_code` for modern teams; you can override later.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path


MULTIWORD_MASCOTS = {
    "Red Sox",
    "White Sox",
    "Blue Jays",
}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def max_schedule_year(retrosheet_dir: Path) -> int | None:
    years = []
    for p in retrosheet_dir.glob("*schedule.csv"):
        m = re.match(r"^(\d{4})schedule\.csv$", p.name)
        if m:
            years.append(int(m.group(1)))
    return max(years) if years else None


def split_franchise_name(franchise: str) -> tuple[str, str]:
    name = clean_text(franchise)

    # Special-case franchises that omit the city.
    if name == "Athletics":
        return "Oakland", "Athletics"

    for mascot in sorted(MULTIWORD_MASCOTS, key=len, reverse=True):
        if name.endswith(" " + mascot):
            return name[: -len(mascot)].strip(), mascot

    parts = name.split(" ")
    if len(parts) < 2:
        return name, name
    return " ".join(parts[:-1]), parts[-1]


def retrosheet_code_for(location: str, team_name: str) -> str:
    loc = clean_text(location)
    name = clean_text(team_name)

    if loc == "Los Angeles" and name == "Angels":
        return "ANA"
    if loc == "Los Angeles" and name == "Dodgers":
        return "LAN"
    if loc == "Chicago" and name == "Cubs":
        return "CHN"
    if loc == "Chicago" and name == "White Sox":
        return "CHA"
    if loc == "New York" and name == "Yankees":
        return "NYA"
    if loc == "New York" and name == "Mets":
        return "NYN"
    if loc == "San Francisco":
        return "SFN"
    if loc == "San Diego":
        return "SDN"
    if loc == "St. Louis":
        return "SLN"
    if loc == "Tampa Bay":
        return "TBA"
    if loc == "Kansas City":
        return "KCA"
    if loc == "Miami":
        return "MIA"
    if loc == "Washington":
        return "WAS"

    compact = re.sub(r"[^A-Za-z]", "", loc)
    return compact[:3].upper()


def main() -> None:
    src = Path("raw/mlb/baseball_reference_franchises.csv")
    retrosheet_dir = Path("raw/mlb/retrosheet")
    out = Path("raw/mlb/mlb_teams.csv")

    max_year = max_schedule_year(retrosheet_dir)

    rows_out = []
    with src.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if not header:
            raise SystemExit(f"Empty file: {src}")

        for row in r:
            if not row:
                continue
            rk = row[0].strip()
            if not rk.isdigit():
                continue

            franchise = clean_text(row[1])
            if not franchise or " see " in franchise.lower():
                continue

            from_year = int(row[2].strip())
            to_year = int(row[3].strip())

            location, team_name = split_franchise_name(franchise)

            to_value = "NULL" if (max_year is not None and to_year == max_year) else str(to_year)

            rows_out.append(
                {
                    "league": "mlb",
                    "location": location,
                    "team_name": team_name,
                    "from": str(from_year),
                    "to": to_value,
                    "franchise_id": f"mlb_franchise_{norm(franchise)}",
                    "retrosheet_code": retrosheet_code_for(location, team_name),
                }
            )

    rows_out.sort(key=lambda x: (x["retrosheet_code"], x["franchise_id"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["league", "location", "team_name", "from", "to", "franchise_id", "retrosheet_code"],
        )
        w.writeheader()
        w.writerows(rows_out)

    print(f"Wrote {len(rows_out)} rows to {out}")


if __name__ == "__main__":
    main()
