#!/usr/bin/env python3
"""Shared NFL team reference data and team_id helpers."""

from __future__ import annotations

import csv
import re
from pathlib import Path

TEAM_REFERENCE = [
    {"name": "Arizona Cardinals", "city": "Arizona", "mascot": "Cardinals", "abbr": "ari", "start_year": 1920, "franchise_id": "nfl_franchise_cardinals"},
    {"name": "Atlanta Falcons", "city": "Atlanta", "mascot": "Falcons", "abbr": "atl", "start_year": 1966, "franchise_id": "nfl_franchise_falcons"},
    {"name": "Baltimore Ravens", "city": "Baltimore", "mascot": "Ravens", "abbr": "bal", "start_year": 1996, "franchise_id": "nfl_franchise_ravens"},
    {"name": "Buffalo Bills", "city": "Buffalo", "mascot": "Bills", "abbr": "buf", "start_year": 1960, "franchise_id": "nfl_franchise_bills"},
    {"name": "Carolina Panthers", "city": "Carolina", "mascot": "Panthers", "abbr": "car", "start_year": 1995, "franchise_id": "nfl_franchise_panthers"},
    {"name": "Chicago Bears", "city": "Chicago", "mascot": "Bears", "abbr": "chi", "start_year": 1920, "franchise_id": "nfl_franchise_bears"},
    {"name": "Cincinnati Bengals", "city": "Cincinnati", "mascot": "Bengals", "abbr": "cin", "start_year": 1968, "franchise_id": "nfl_franchise_bengals"},
    {"name": "Cleveland Browns", "city": "Cleveland", "mascot": "Browns", "abbr": "cle", "start_year": 1946, "franchise_id": "nfl_franchise_browns"},
    {"name": "Dallas Cowboys", "city": "Dallas", "mascot": "Cowboys", "abbr": "dal", "start_year": 1960, "franchise_id": "nfl_franchise_cowboys"},
    {"name": "Denver Broncos", "city": "Denver", "mascot": "Broncos", "abbr": "den", "start_year": 1960, "franchise_id": "nfl_franchise_broncos"},
    {"name": "Detroit Lions", "city": "Detroit", "mascot": "Lions", "abbr": "det", "start_year": 1930, "franchise_id": "nfl_franchise_lions"},
    {"name": "Green Bay Packers", "city": "Green Bay", "mascot": "Packers", "abbr": "gb", "start_year": 1921, "franchise_id": "nfl_franchise_packers"},
    {"name": "Houston Texans", "city": "Houston", "mascot": "Texans", "abbr": "hou", "start_year": 2002, "franchise_id": "nfl_franchise_texans"},
    {"name": "Indianapolis Colts", "city": "Indianapolis", "mascot": "Colts", "abbr": "ind", "start_year": 1953, "franchise_id": "nfl_franchise_colts"},
    {"name": "Jacksonville Jaguars", "city": "Jacksonville", "mascot": "Jaguars", "abbr": "jax", "start_year": 1995, "franchise_id": "nfl_franchise_jaguars"},
    {"name": "Kansas City Chiefs", "city": "Kansas City", "mascot": "Chiefs", "abbr": "kc", "start_year": 1960, "franchise_id": "nfl_franchise_chiefs"},
    {"name": "Las Vegas Raiders", "city": "Las Vegas", "mascot": "Raiders", "abbr": "lv", "start_year": 1960, "franchise_id": "nfl_franchise_raiders"},
    {"name": "Los Angeles Chargers", "city": "Los Angeles", "mascot": "Chargers", "abbr": "lac", "start_year": 1960, "franchise_id": "nfl_franchise_chargers"},
    {"name": "Los Angeles Rams", "city": "Los Angeles", "mascot": "Rams", "abbr": "lar", "start_year": 1937, "franchise_id": "nfl_franchise_rams"},
    {"name": "Miami Dolphins", "city": "Miami", "mascot": "Dolphins", "abbr": "mia", "start_year": 1966, "franchise_id": "nfl_franchise_dolphins"},
    {"name": "Minnesota Vikings", "city": "Minnesota", "mascot": "Vikings", "abbr": "min", "start_year": 1961, "franchise_id": "nfl_franchise_vikings"},
    {"name": "New England Patriots", "city": "New England", "mascot": "Patriots", "abbr": "ne", "start_year": 1960, "franchise_id": "nfl_franchise_patriots"},
    {"name": "New Orleans Saints", "city": "New Orleans", "mascot": "Saints", "abbr": "no", "start_year": 1967, "franchise_id": "nfl_franchise_saints"},
    {"name": "New York Giants", "city": "New York", "mascot": "Giants", "abbr": "nyg", "start_year": 1925, "franchise_id": "nfl_franchise_giants"},
    {"name": "New York Jets", "city": "New York", "mascot": "Jets", "abbr": "nyj", "start_year": 1960, "franchise_id": "nfl_franchise_jets"},
    {"name": "Philadelphia Eagles", "city": "Philadelphia", "mascot": "Eagles", "abbr": "phi", "start_year": 1933, "franchise_id": "nfl_franchise_eagles"},
    {"name": "Pittsburgh Steelers", "city": "Pittsburgh", "mascot": "Steelers", "abbr": "pit", "start_year": 1933, "franchise_id": "nfl_franchise_steelers"},
    {"name": "San Francisco 49ers", "city": "San Francisco", "mascot": "49ers", "abbr": "sf", "start_year": 1946, "franchise_id": "nfl_franchise_49ers"},
    {"name": "Seattle Seahawks", "city": "Seattle", "mascot": "Seahawks", "abbr": "sea", "start_year": 1976, "franchise_id": "nfl_franchise_seahawks"},
    {"name": "Tampa Bay Buccaneers", "city": "Tampa Bay", "mascot": "Buccaneers", "abbr": "tb", "start_year": 1976, "franchise_id": "nfl_franchise_buccaneers"},
    {"name": "Tennessee Titans", "city": "Tennessee", "mascot": "Titans", "abbr": "ten", "start_year": 1960, "franchise_id": "nfl_franchise_titans"},
    {"name": "Washington Commanders", "city": "Washington", "mascot": "Commanders", "abbr": "was", "start_year": 1932, "franchise_id": "nfl_franchise_commanders"},
]

NAME_TO_REF = {row["name"]: row for row in TEAM_REFERENCE}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def default_city_prefix(city: str) -> str:
    compact = norm(city).replace("_", "")
    return compact[:3]


def load_city_prefix_overrides(path: str | Path, league: str = "NFL") -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}

    def city_key(value: str) -> str:
        return " ".join(value.strip().split())

    out: dict[str, str] = {}
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("league", "").strip().upper() != league.upper():
                continue
            city = city_key(row.get("city", ""))
            prefix = row.get("prefix", "").strip().lower()
            if city and prefix:
                out[city] = prefix
    return out


def team_id_for(league: str, city: str, mascot: str, city_prefix_overrides: dict[str, str] | None = None) -> str:
    overrides = city_prefix_overrides or {}
    city_norm = " ".join(city.strip().split())
    prefix = overrides.get(city_norm, default_city_prefix(city_norm))
    return f"{league.lower()}_{prefix}_{norm(mascot)}"
