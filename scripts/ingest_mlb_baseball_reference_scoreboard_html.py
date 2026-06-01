#!/usr/bin/env python3
"""Update MLB team_games with final scores from a Baseball-Reference scoreboard HTML dump.

Input:
- A local HTML snippet copied from baseball-reference.com that contains per-day
  "Standings & Scores" listings with <p class="game"> blocks.
- Retrosheet schedule CSV for the season (raw/mlb/retrosheet/<season>schedule.csv)
  to resolve the visiting team code and doubleheader game numbers.

Why schedule join is needed:
- The HTML uses Baseball-Reference /teams/<code>/... abbreviations for teams, but the
  boxscore link embeds the *Retrosheet* home code:
    /boxes/SFN/SFN202603250.shtml  -> home=SFN, date=20260325, game_num=0
- We use the schedule to map (date, home_code, game_num) -> visitor_code, (v_game,h_game)
  and then build the deterministic game_id already used by ingest_mlb_retrosheet_schedule.py.

This script is intended to fill in results for an in-progress season where only
the schedule has been ingested (scores NULL).
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class TeamEra:
    code: str
    team_id: str
    league: str
    start_year: int
    end_year: int | None


@dataclass(frozen=True)
class ScheduleRow:
    date: str  # YYYY-MM-DD
    visitor: str  # retrosheet code
    home: str  # retrosheet code
    v_game: str  # may be blank
    h_game: str  # may be blank


@dataclass(frozen=True)
class ParsedGame:
    date: str  # YYYY-MM-DD
    home: str  # retrosheet code (from boxscore link)
    game_num: int  # from boxscore link (0 for single game)
    away_score: int
    home_score: int
    boxscore_href: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--teams-csv", default="raw/mlb/mlb_teams.csv")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--schedule-csv", default=None, help="Override schedule CSV path")
    p.add_argument("--html", default="raw/mlb/baseball_reference/2026_04_14.html")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_end_year(value: str) -> int | None:
    v = value.strip()
    if not v or v.upper() == "NULL":
        return None
    return int(v)


def team_id_for_row(code: str, location: str, team_name: str) -> str:
    # Must match scripts/import_mlb_teams_csv.py
    return f"mlb_{code.lower()}_{norm(location)}_{norm(team_name)}"


def load_team_eras(path: Path) -> list[TeamEra]:
    eras: list[TeamEra] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("league", "").strip().upper() != "MLB":
                continue
            code = row["retrosheet_code"].strip().upper()
            team_name = " ".join(row["team_name"].strip().split())
            location = " ".join(row["location"].strip().split())
            start = int(row["from"].strip())
            end = parse_end_year(row["to"])
            eras.append(
                TeamEra(
                    code=code,
                    team_id=team_id_for_row(code, location, team_name),
                    league="MLB",
                    start_year=start,
                    end_year=end,
                )
            )
    return eras


def resolve_team_id(code: str, season: int, eras: list[TeamEra]) -> str:
    matches = [
        e
        for e in eras
        if e.code == code and e.start_year <= season and (e.end_year is None or e.end_year >= season)
    ]
    if not matches:
        raise ValueError(f"No MLB team era found for code={code} season={season}")
    if len(matches) > 1:
        raise ValueError(f"Ambiguous MLB team era for code={code} season={season}: {matches}")
    return matches[0].team_id


def yyyymmdd_to_date(value: str) -> str:
    v = value.strip().strip('"')
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"


def load_schedule(path: Path) -> tuple[dict[tuple[str, str, int], list[ScheduleRow]], dict[tuple[str, str], list[ScheduleRow]]]:
    by_key: dict[tuple[str, str, int], list[ScheduleRow]] = {}
    by_date_home: dict[tuple[str, str], list[ScheduleRow]] = {}

    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if not header:
            return by_key, by_date_home

        try:
            i_date = header.index("Date")
            i_visitor = header.index("Visitor")
            i_home = header.index("Home")
            i_postponed = header.index("Postponed")
        except ValueError as e:
            raise ValueError(f"Unexpected Retrosheet schedule header in {path}: {header}") from e

        game_idxs = [i for i, h in enumerate(header) if h == "Game"]
        if len(game_idxs) >= 2:
            i_v_game, i_h_game = game_idxs[0], game_idxs[1]
        elif len(game_idxs) == 1:
            i_v_game = i_h_game = game_idxs[0]
        else:
            i_v_game = i_h_game = -1

        for row in r:
            if not row or len(row) <= i_postponed:
                continue
            if row[i_postponed].strip():
                continue
            date = yyyymmdd_to_date(row[i_date])
            visitor = row[i_visitor].strip().strip('"').upper()
            home = row[i_home].strip().strip('"').upper()
            v_game = row[i_v_game].strip() if i_v_game >= 0 and i_v_game < len(row) else ""
            h_game = row[i_h_game].strip() if i_h_game >= 0 and i_h_game < len(row) else ""

            # Normalize blank to 0 for matching Baseball-Reference boxscore suffix.
            try:
                h_game_num = int(h_game) if h_game else 0
            except ValueError:
                h_game_num = 0

            sr = ScheduleRow(date=date, visitor=visitor, home=home, v_game=v_game, h_game=h_game)
            by_key.setdefault((date, home, h_game_num), []).append(sr)
            by_date_home.setdefault((date, home), []).append(sr)

    return by_key, by_date_home


BOX_RE = re.compile(r'/boxes/([A-Z0-9]{3})/\1(\d{8})(\d)\.shtml')


def parse_scoreboard_html(path: Path) -> list[ParsedGame]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    games: list[ParsedGame] = []

    # Iterate by date sections to keep parsing robust.
    for m in re.finditer(r"<h3>[^<]*</h3>.*?(?=<h3>|\Z)", txt, flags=re.S):
        section = m.group(0)
        for pm in re.finditer(r'<p class="game">(.*?)</p>', section, flags=re.S):
            gtxt = pm.group(1)

            box = BOX_RE.search(gtxt)
            if not box:
                continue
            home = box.group(1).upper()
            ymd = box.group(2)
            date = yyyymmdd_to_date(ymd)
            game_num = int(box.group(3))
            href = box.group(0)

            # Team codes and scores are ordered: away, home.
            codes = re.findall(r"/teams/([A-Z0-9]{2,3})/", gtxt)
            scores = [int(x) for x in re.findall(r"\((\d+)\)", gtxt)]
            if len(codes) < 2 or len(scores) < 2:
                continue

            games.append(
                ParsedGame(
                    date=date,
                    home=home,
                    game_num=game_num,
                    away_score=scores[0],
                    home_score=scores[1],
                    boxscore_href=href,
                )
            )

    return games


def pick_schedule_row(
    pg: ParsedGame,
    by_key: dict[tuple[str, str, int], list[ScheduleRow]],
    by_date_home: dict[tuple[str, str], list[ScheduleRow]],
) -> ScheduleRow | None:
    cand = by_key.get((pg.date, pg.home, pg.game_num), [])
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        # Shouldn't happen; fall back to unique visitor if possible.
        uniq = {(c.visitor, c.v_game, c.h_game) for c in cand}
        if len(uniq) == 1:
            return cand[0]
        return None

    # Fallback: if there is exactly one game for this home on this date, take it.
    dh = by_date_home.get((pg.date, pg.home), [])
    if len(dh) == 1:
        return dh[0]
    return None


def main() -> None:
    args = parse_args()

    schedule_path = Path(args.schedule_csv) if args.schedule_csv else Path("raw/mlb/retrosheet") / f"{args.season}schedule.csv"
    if not schedule_path.exists():
        raise SystemExit(f"Schedule CSV not found: {schedule_path}")

    eras = load_team_eras(Path(args.teams_csv))
    by_key, by_date_home = load_schedule(schedule_path)

    parsed = parse_scoreboard_html(Path(args.html))
    if not parsed:
        raise SystemExit(f"No games parsed from HTML: {args.html}")

    updates: list[tuple[int, int, str, str, str]] = []
    unmatched: list[ParsedGame] = []

    for pg in parsed:
        sr = pick_schedule_row(pg, by_key, by_date_home)
        if not sr:
            unmatched.append(pg)
            continue

        visitor_team_id = resolve_team_id(sr.visitor, args.season, eras)
        home_team_id = resolve_team_id(sr.home, args.season, eras)

        date = pg.date
        game_id = f"mlb_{args.season}_{date}_{sr.visitor}_{sr.home}_{sr.v_game}_{sr.h_game}"

        if pg.away_score == pg.home_score:
            v_res, h_res = "T", "T"
        elif pg.away_score > pg.home_score:
            v_res, h_res = "W", "L"
        else:
            v_res, h_res = "L", "W"

        updates.append((pg.away_score, pg.home_score, v_res, game_id, visitor_team_id))
        updates.append((pg.home_score, pg.away_score, h_res, game_id, home_team_id))

    if unmatched:
        sample = unmatched[:10]
        details = ", ".join(f"{g.date} home={g.home} num={g.game_num} href={g.boxscore_href}" for g in sample)
        print(f"Unmatched games: {len(unmatched)} (sample: {details})")

    print(f"Parsed games: {len(parsed)}; matched: {len(updates)//2}; team-row updates: {len(updates)}")
    if args.dry_run:
        return

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    con.execute("BEGIN TRANSACTION")
    con.executemany(
        """
        UPDATE team_games
        SET pts_for=?, pts_against=?, result=?
        WHERE league='MLB' AND game_id=? AND team_id=?
        """,
        updates,
    )
    con.execute("COMMIT")

    # Report how many MLB 2026 rows still have NULL results after update.
    remaining = con.execute(
        "SELECT COUNT(*) FROM team_games WHERE league='MLB' AND season=? AND result IS NULL",
        [args.season],
    ).fetchone()[0]
    print(f"Remaining MLB season {args.season} team-game rows with NULL result: {remaining}")


if __name__ == "__main__":
    main()
