#!/usr/bin/env python3
"""Mark championship-clinching wins.

Definition:
- For each (league, season), find the last postseason game by date.
- Mark the winning team's row for that game_id as is_championship_clinching = TRUE.

Guardrails:
- If multiple distinct games occur on the final postseason date for a season, this is
  treated as an anomaly. By default we pick a deterministic game_id (max lexical) and
  report it; with --strict we skip the season instead.
- If a chosen final game has no single 'W' row, we skip and report.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class SeasonOutcome:
    league: str
    season: int
    final_date: str
    final_game_id: str
    champion_team_id: str
    anomaly: str | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/whenwin.duckdb")
    p.add_argument("--schema", default="sql/schema.sql")
    p.add_argument("--leagues", default="MLB,NBA,NFL,NHL", help="Comma-separated list")
    p.add_argument("--from-season", type=int, default=1978)
    p.add_argument("--to-season", type=int, default=None)
    p.add_argument("--strict", action="store_true", help="Skip seasons with ambiguous final date")
    return p.parse_args()


def ensure_schema(con: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    con.execute(schema_path.read_text(encoding="utf-8"))
    # CREATE TABLE IF NOT EXISTS won't add columns, so do it explicitly for existing DBs.
    con.execute("ALTER TABLE team_games ADD COLUMN IF NOT EXISTS is_championship_clinching BOOLEAN")


def main() -> None:
    args = parse_args()
    leagues = [x.strip().upper() for x in args.leagues.split(",") if x.strip()]

    con = duckdb.connect(str(Path(args.db)))
    ensure_schema(con, Path(args.schema))

    max_season = args.to_season
    if max_season is None:
        max_season = con.execute("select max(season) from team_games").fetchone()[0] or args.from_season

    seasons = list(range(args.from_season, int(max_season) + 1))

    # Reset existing markers in scope.
    con.execute(
        """
        UPDATE team_games
        SET is_championship_clinching = FALSE
        WHERE league IN (SELECT * FROM UNNEST(?))
          AND season IN (SELECT * FROM UNNEST(?))
        """,
        [leagues, seasons],
    )

    outcomes: list[SeasonOutcome] = []
    anomalies: list[str] = []

    for league in leagues:
        for season in seasons:
            final_date_row = con.execute(
                """
                SELECT max(date)
                FROM team_games
                WHERE league = ?
                  AND season = ?
                  AND game_type = 'postseason'
                """,
                [league, season],
            ).fetchone()
            final_date = final_date_row[0]
            if final_date is None:
                continue

            game_ids = [
                r[0]
                for r in con.execute(
                    """
                    SELECT DISTINCT game_id
                    FROM team_games
                    WHERE league = ?
                      AND season = ?
                      AND game_type = 'postseason'
                      AND date = ?
                    ORDER BY game_id
                    """,
                    [league, season, final_date],
                ).fetchall()
            ]
            if not game_ids:
                continue

            anomaly: str | None = None
            if len(game_ids) > 1:
                anomaly = f"ambiguous_final_date_games={len(game_ids)}"
                msg = f"{league} season={season}: {len(game_ids)} postseason games on final date {final_date}; ids={game_ids[:5]}{'...' if len(game_ids)>5 else ''}"
                anomalies.append(msg)
                if args.strict:
                    continue

            final_game_id = game_ids[-1]  # deterministic (sorted ascending)

            winners = [
                r[0]
                for r in con.execute(
                    """
                    SELECT team_id
                    FROM team_games
                    WHERE league = ?
                      AND season = ?
                      AND game_type = 'postseason'
                      AND game_id = ?
                      AND result = 'W'
                    """,
                    [league, season, final_game_id],
                ).fetchall()
            ]
            if len(winners) != 1:
                anomalies.append(
                    f"{league} season={season}: final_game_id={final_game_id} has winners={winners} (expected exactly 1)"
                )
                continue

            champion_team_id = winners[0]
            con.execute(
                """
                UPDATE team_games
                SET is_championship_clinching = TRUE
                WHERE league = ?
                  AND season = ?
                  AND game_type = 'postseason'
                  AND game_id = ?
                  AND team_id = ?
                """,
                [league, season, final_game_id, champion_team_id],
            )

            outcomes.append(
                SeasonOutcome(
                    league=league,
                    season=season,
                    final_date=str(final_date),
                    final_game_id=final_game_id,
                    champion_team_id=champion_team_id,
                    anomaly=anomaly,
                )
            )

    # Summary
    print(f"Marked championship clinchers for {len(outcomes)} league-seasons.")
    if anomalies:
        print(f"Anomalies encountered: {len(anomalies)} (showing up to 20)")
        for line in anomalies[:20]:
            print(f"- {line}")

    # Sanity: at most one clincher per league-season.
    bad = con.execute(
        """
        SELECT league, season, COUNT(*) AS flagged_rows
        FROM team_games
        WHERE is_championship_clinching = TRUE
        GROUP BY league, season
        HAVING flagged_rows != 1
        ORDER BY league, season
        """
    ).fetchall()
    if bad:
        print("WARNING: Some league-seasons do not have exactly 1 championship clincher row flagged:")
        for r in bad[:20]:
            print(f"- {r}")


if __name__ == "__main__":
    main()

