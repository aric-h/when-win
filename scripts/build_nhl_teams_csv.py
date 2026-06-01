#!/usr/bin/env python3
"""Build an NHL team identity CSV from Hockey-Reference franchise tables.

Inputs:
- raw/nhl/hockey-reference/franchises.csv
- raw/nhl/hockey-reference/defunct_franchises.csv

Output (similar to nfl_teams.csv):
- raw/nhl/hockey-reference/nhl_teams.csv with columns:
  league,location,team_name,from,to,franchise_id

Notes:
- Filters to rows where Lg == 'NHL'.
- Converts `to` to NULL when it matches the max season found in `raw/nhl/hockey-reference/*.csv` game logs.
- Derives a root `franchise_id` by interval containment: every row maps to the smallest interval that contains it.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FranchiseRow:
    name: str
    league: str
    start_year: int
    end_year: int


MULTIWORD_MASCOTS = {
    "Blue Jackets",
    "Black Hawks",
    "Golden Knights",
    "Golden Seals",
    "Maple Leafs",
    "North Stars",
    "Red Wings",
}

STOPWORDS = {"of", "the", "de", "la", "st", "st.", "saint"}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def max_season_in_game_logs(dir_path: Path) -> int | None:
    seasons: list[int] = []
    for p in dir_path.glob("*.csv"):
        m = re.match(r"^(\d{4})(?:_playoffs)?\.csv$", p.name)
        if not m:
            continue
        seasons.append(int(m.group(1)))
    return max(seasons) if seasons else None


def parse_team_name(franchise_name: str) -> tuple[str, str]:
    name = " ".join(franchise_name.strip().split())

    # Handle patterns like "Mighty Ducks of Anaheim".
    if " of " in name:
        left, right = name.rsplit(" of ", 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            return right, left

    for mascot in sorted(MULTIWORD_MASCOTS, key=len, reverse=True):
        if name.endswith(" " + mascot):
            return name[: -len(mascot)].strip(), mascot

    parts = name.split(" ")
    if len(parts) < 2:
        return name, name
    return " ".join(parts[:-1]), parts[-1]


def read_franchise_csv(path: Path) -> list[FranchiseRow]:
    rows: list[FranchiseRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            lg = row.get("Lg", "").strip().upper()
            if lg != "NHL":
                continue
            name = row["Franchise"].strip()
            start_year = int(row["From"].strip())
            end_year = int(row["To"].strip())
            rows.append(FranchiseRow(name=name, league=lg, start_year=start_year, end_year=end_year))
    return rows


def token_set(franchise_name: str) -> set[str]:
    # Used for grouping likely name-changes; not intended to perfectly capture relocations.
    name = " ".join(franchise_name.strip().split()).lower()
    words = re.findall(r"[a-z0-9]+", name)
    return {w for w in words if w not in STOPWORDS}


def interval_contains(a: FranchiseRow, b: FranchiseRow) -> bool:
    return a.start_year <= b.start_year and a.end_year >= b.end_year


def intervals_touch(a: FranchiseRow, b: FranchiseRow) -> bool:
    # Consider same-year handoffs as touching (e.g., 1993 -> 1993).
    return a.end_year == b.start_year or b.end_year == a.start_year


class DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def build_components(rows: list[FranchiseRow]) -> dict[FranchiseRow, FranchiseRow]:
    # Best-effort franchise grouping.
    # We primarily group name-change rows and their \"full franchise\" rows. Relocations with no shared
    # tokens (e.g., Nordiques -> Avalanche) will generally need manual mapping later.
    tokens = [token_set(r.name) for r in rows]
    parsed = [parse_team_name(r.name) for r in rows]
    dsu = DSU(len(rows))

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a = rows[i]
            b = rows[j]

            # Containment: connect a full-span row to its sub-rows when the names share non-trivial tokens.
            if interval_contains(a, b) or interval_contains(b, a):
                overlap = tokens[i].intersection(tokens[j]) - {"new", "york", "st", "saint"}
                if overlap:
                    dsu.union(i, j)
                    continue

            # Touching: connect sequential identities that hand off directly and share a location.
            if intervals_touch(a, b):
                (a_loc, _a_mascot) = parsed[i]
                (b_loc, _b_mascot) = parsed[j]
                if a_loc.lower() == b_loc.lower():
                    dsu.union(i, j)

    comps: dict[int, list[FranchiseRow]] = {}
    for idx, r in enumerate(rows):
        root = dsu.find(idx)
        comps.setdefault(root, []).append(r)

    def score(r: FranchiseRow) -> tuple[int, int, str]:
        # Prefer earliest start, then latest end, then lexicographic name.
        return (r.start_year, -r.end_year, r.name)

    row_to_component_root: dict[FranchiseRow, FranchiseRow] = {}
    for members in comps.values():
        # Canonical is the broadest span within the component.
        members_sorted = sorted(members, key=lambda r: (r.start_year, -r.end_year, r.name))
        # Choose member with min start and max end among those with min start.
        min_start = members_sorted[0].start_year
        candidates = [m for m in members_sorted if m.start_year == min_start]
        canonical = max(candidates, key=lambda r: (r.end_year, r.name))
        for m in members:
            row_to_component_root[m] = canonical

    return row_to_component_root


def main() -> None:
    base_dir = Path("raw/nhl/hockey-reference")
    franchises_path = base_dir / "franchises.csv"
    defunct_path = base_dir / "defunct_franchises.csv"
    out_path = base_dir / "nhl_teams.csv"

    rows = read_franchise_csv(franchises_path) + read_franchise_csv(defunct_path)
    if not rows:
        raise SystemExit("No NHL rows found in input franchise CSVs")

    max_season = max_season_in_game_logs(base_dir)

    roots = build_components(rows)

    out_rows = []
    for r in rows:
        location, team_name = parse_team_name(r.name)
        to_value = "NULL" if (max_season is not None and r.end_year == max_season) else str(r.end_year)

        root = roots.get(r, r)
        franchise_id = f"nhl_franchise_{norm(root.name)}"

        out_rows.append(
            {
                "league": "nhl",
                "location": location,
                "team_name": team_name,
                "from": str(r.start_year),
                "to": to_value,
                "franchise_id": franchise_id,
            }
        )

    # Deterministic ordering.
    out_rows.sort(key=lambda x: (x["franchise_id"], int(x["from"]), x["location"], x["team_name"]))

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["league", "location", "team_name", "from", "to", "franchise_id"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
