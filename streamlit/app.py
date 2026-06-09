from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

import streamlit as st

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "local_data" / "whenwin.duckdb"


def get_db_path() -> str:
    return os.environ.get("WHENWIN_DB", str(DEFAULT_DB_PATH))


@st.cache_resource
def get_con(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


@st.cache_data(ttl=60)
def load_location_groups(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(
        """
        SELECT location_group_id, name
        FROM location_groups
        ORDER BY name
        """
    ).df()


def build_location_game_days_sql() -> str:
    # Keep this query self-contained so it can be moved into a view later if desired.
    return """
WITH dedup AS (
  SELECT DISTINCT
    tg.date,
    tlg.location_group_id,
    tg.league,
    tg.team_id,
    t.team_name,
    tg.result,
    tg.game_type,
    COALESCE(tg.is_series_clinching, FALSE)       AS is_series_clinching,
    COALESCE(tg.is_championship_clinching, FALSE)  AS is_championship_clinching
  FROM team_games tg
  JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
  JOIN teams t ON t.team_id = tg.team_id
  WHERE tg.date IS NOT NULL
    AND tg.result IS NOT NULL
),
daily AS (
  SELECT
    date,
    location_group_id,
    COUNT(DISTINCT team_id) AS teams_playing,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN team_id END) AS winners,
    COUNT(DISTINCT CASE WHEN result = 'L' THEN team_id END) AS losers,
    COUNT(DISTINCT CASE WHEN result = 'T' THEN team_id END) AS ties,
    COUNT(DISTINCT league) AS leagues_playing,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN league END) AS leagues_winning,
    MAX(CASE WHEN game_type = 'postseason' THEN 1 ELSE 0 END) = 1 AS has_playoff_games,
    MIN(CASE WHEN game_type = 'postseason' THEN 1 ELSE 0 END) = 1 AS all_games_playoffs,
    COUNT(DISTINCT CASE WHEN is_series_clinching AND result='W' THEN team_id END) AS series_clinching_wins,
    COUNT(DISTINCT CASE WHEN is_championship_clinching AND result='W' THEN team_id END) AS championship_clinching_wins
  FROM dedup
  GROUP BY 1,2
)
SELECT
  d.*,
  lg.name AS location_group_name,
  CASE WHEN d.winners = d.teams_playing THEN 'Sweep' ELSE 'Partial' END AS sweep_status
FROM daily d
LEFT JOIN location_groups lg ON lg.location_group_id = d.location_group_id
WHERE d.winners >= 3
  AND d.leagues_winning >= 3
"""


@st.cache_data(ttl=60)
def load_location_game_days(
    db_path: str,
    location_group_id: str | None,
    playoffs_filter: str,
    clinch_filter: str,
    min_date: date | None,
    max_date: date | None,
) -> pd.DataFrame:
    con = get_con(db_path)
    sql = build_location_game_days_sql()

    where = []
    params: list[object] = []

    if location_group_id and location_group_id != "__all__":
        where.append("location_group_id = ?")
        params.append(location_group_id)

    if min_date:
        where.append("date >= ?")
        params.append(min_date.isoformat())
    if max_date:
        where.append("date <= ?")
        params.append(max_date.isoformat())

    if playoffs_filter == "Has Playoff Games":
        where.append("has_playoff_games")
    elif playoffs_filter == "All Games Playoffs":
        where.append("all_games_playoffs")
    elif playoffs_filter == "No Playoff Games":
        where.append("NOT has_playoff_games")

    if clinch_filter == "Series Clinchers":
        where.append("series_clinching_wins >= 1")
    elif clinch_filter == "Championship Clinchers":
        where.append("championship_clinching_wins >= 1")
    elif clinch_filter == "Any Clinchers":
        where.append("(series_clinching_wins + championship_clinching_wins) >= 1")
    elif clinch_filter == "2+ Clinchers":
        where.append("(series_clinching_wins + championship_clinching_wins) >= 2")

    if where:
        sql = f"WITH q AS ({sql}) SELECT * FROM q WHERE " + " AND ".join(where)
    else:
        sql = f"WITH q AS ({sql}) SELECT * FROM q"

    sql += " ORDER BY date DESC, winners DESC, leagues_winning DESC, location_group_id"
    return con.execute(sql, params).df()


@st.cache_data(ttl=60)
def load_day_games(db_path: str, day: str, location_group_id: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(
        """
        SELECT
          tg.date,
          tg.league,
          tg.season,
          tg.game_id,
          tg.team_id,
          t.city || ' ' || t.team_name AS team_label,
          tg.opponent_team_id,
          o.city || ' ' || o.team_name AS opponent_label,
          tg.result,
          tg.pts_for,
          tg.pts_against,
          tg.game_type,
          pgr.round_order AS playoff_round_order,
          pgr.round_name  AS playoff_round,
          COALESCE(tg.is_series_clinching, FALSE)      AS is_series_clinching,
          COALESCE(tg.is_championship_clinching, FALSE) AS is_championship_clinching
        FROM team_games tg
        JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
        JOIN teams t ON t.team_id = tg.team_id
        JOIN teams o ON o.team_id = tg.opponent_team_id
        LEFT JOIN postseason_game_rounds pgr
          ON pgr.league = tg.league AND pgr.game_id = tg.game_id
        WHERE tg.date = ?
          AND tlg.location_group_id = ?
          AND tg.result IS NOT NULL
        ORDER BY tg.league, tg.game_type DESC, team_label
        """,
        [day, location_group_id],
    ).df()


def main() -> None:
    st.set_page_config(page_title="WhenWin (Local)", layout="wide")
    st.title("WhenWin — 3+ Win Days by Region")

    db_path = get_db_path()
    if not Path(db_path).exists():
        st.error(f"DuckDB file not found: {db_path}")
        st.stop()

    with st.sidebar:
        st.header("Filters")
        groups = load_location_groups(db_path)
        options = ["__all__"] + groups["location_group_id"].tolist()
        labels = {"__all__": "All locations"} | dict(
            zip(groups["location_group_id"], groups["name"])
        )
        location = st.selectbox(
            "Location Group",
            options=options,
            format_func=lambda x: labels.get(x, x),
        )

        playoffs_filter = st.selectbox(
            "Playoffs",
            ["Any", "Has Playoff Games", "All Games Playoffs", "No Playoff Games"],
        )
        clinch_filter = st.selectbox(
            "Clinching Wins",
            [
                "Any",
                "Any Clinchers",
                "2+ Clinchers",
                "Series Clinchers",
                "Championship Clinchers",
            ],
        )

        min_date = st.date_input("From", value=None)
        max_date = st.date_input("To", value=None)
        if isinstance(min_date, tuple):
            min_date = None
        if isinstance(max_date, tuple):
            max_date = None

    df = load_location_game_days(
        db_path=db_path,
        location_group_id=location,
        playoffs_filter=playoffs_filter,
        clinch_filter=clinch_filter,
        min_date=min_date,
        max_date=max_date,
    )

    st.caption(f"DB: `{db_path}` • Rows: {len(df)}")

    if df.empty:
        st.info("No results for the selected filters.")
        return

    latest = df.iloc[0]
    st.info(
        f"Latest: {latest['date']} — {latest['location_group_name']} "
        f"({int(latest['winners'])} wins / {int(latest['leagues_winning'])} leagues; {latest['sweep_status']})"
    )

    df_view = df[
        [
            "date",
            "location_group_name",
            "location_group_id",
            "winners",
            "teams_playing",
            "leagues_winning",
            "leagues_playing",
            "sweep_status",
            "has_playoff_games",
            "all_games_playoffs",
            "series_clinching_wins",
            "championship_clinching_wins",
        ]
    ]

    selection = st.dataframe(
        df_view.drop(columns=["location_group_id"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    st.divider()
    st.subheader("Day Detail")

    if selection and selection.selection and selection.selection.get("rows"):
        i = selection.selection["rows"][0]
        chosen = df.iloc[i]
        chosen_day = str(chosen["date"])
        chosen_loc = str(chosen["location_group_id"])
        chosen_name = str(chosen["location_group_name"])
    else:
        chosen_day = str(latest["date"])
        chosen_loc = str(latest["location_group_id"])
        chosen_name = str(latest["location_group_name"])

    st.caption(f"{chosen_day} — {chosen_name} (`{chosen_loc}`)")
    games = load_day_games(db_path, chosen_day, chosen_loc)

    if games.empty:
        st.info("No games found for that date/location (or results not populated).")
        return

    # Display per-league sections.
    for league in ["MLB", "NBA", "NFL", "NHL"]:
        g = games[games["league"] == league]
        if g.empty:
            continue
        st.markdown(f"### {league}")
        st.dataframe(
            g[
                [
                    "game_type",
                    "team_label",
                    "result",
                    "pts_for",
                    "pts_against",
                    "opponent_label",
                    "playoff_round",
                    "is_series_clinching",
                    "is_championship_clinching",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
