from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd

import streamlit as st

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "local_data" / "whenwin.duckdb"
SQL_DIR = Path(__file__).resolve().parent / "sql"

# Earliest date in the dataset (static — historical data does not change)
MIN_DATE = date(1978, 10, 1)

MONTH_LABELS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


# ── SQL loader ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=None)
def _read_sql(name: str) -> str:
    """Read and cache a .sql file from the sql/ directory."""
    path = SQL_DIR / f"{name}.sql"
    return path.read_text()


# ── DB helpers ──────────────────────────────────────────────────────────────


def get_db_path() -> str:
    return os.environ.get("WHENWIN_DB", str(DEFAULT_DB_PATH))


@st.cache_resource
def get_con(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


@st.cache_data(ttl=60)
def load_location_groups(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("location_groups")).df()


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
    sql = _read_sql("location_game_days")

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
def load_all_location_game_days(db_path: str) -> pd.DataFrame:
    """Load all location game days with no filters (for the leaderboard)."""
    con = get_con(db_path)
    sql = _read_sql("location_game_days")
    sql = f"WITH q AS ({sql}) SELECT * FROM q"
    sql += " ORDER BY date DESC, winners DESC, leagues_winning DESC, location_group_id"
    return con.execute(sql).df()


@st.cache_data(ttl=60)
def load_game_days(db_path: str, day: str, location_group_id: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("game_days"), [day, location_group_id]).df()


@st.cache_data(ttl=60)
def load_instances_by_year(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("instances_by_year")).df()


@st.cache_data(ttl=60)
def load_instances_by_year_month(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("instances_by_year_month")).df()


@st.cache_data(ttl=60)
def load_instances_by_calendar_day(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("instances_by_calendar_day")).df()


def main() -> None:
    st.set_page_config(page_title="WhenWin", layout="wide")
    st.title("🏆 WhenWin")
    st.markdown(
        "Every day since 1978 where **3 or more teams from the same city "
        "each won a game across 3+ major leagues** (MLB, NBA, NFL, NHL). "
        "Filter the table below by location, playoff status, clinching wins, "
        "or date range. Select any row to see full game details."
    )

    db_path = get_db_path()
    if not Path(db_path).exists():
        st.error(f"DuckDB file not found: {db_path}")
        st.stop()

    # ── Inline filters (no sidebar) ────────────────────────────────────────
    groups = load_location_groups(db_path)
    options = ["__all__"] + groups["location_group_id"].tolist()
    labels = {"__all__": "All locations"} | dict(
        zip(groups["location_group_id"], groups["name"])
    )

    max_date_bound = date.today()

    # Session-state keys for each filter widget
    _KEY_LOCATION = "filter_location"
    _KEY_PLAYOFFS = "filter_playoffs"
    _KEY_CLINCH = "filter_clinch"
    _KEY_DATE = "filter_date_range"

    def _reset_filters() -> None:
        """Set all filter keys to their default values."""
        st.session_state[_KEY_LOCATION] = "__all__"
        st.session_state[_KEY_PLAYOFFS] = "Any"
        st.session_state[_KEY_CLINCH] = "Any"
        st.session_state[_KEY_DATE] = (MIN_DATE, max_date_bound)

    filter_cols = st.columns([2, 2, 2, 2, 1])
    with filter_cols[0]:
        location = st.selectbox(
            "Location",
            options=options,
            format_func=lambda x: labels.get(x, x),
            key=_KEY_LOCATION,
        )
    with filter_cols[1]:
        playoffs_filter = st.selectbox(
            "Playoffs",
            ["Any", "Has Playoff Games", "All Games Playoffs", "No Playoff Games"],
            key=_KEY_PLAYOFFS,
        )
    with filter_cols[2]:
        clinch_filter = st.selectbox(
            "Clinching Wins",
            [
                "Any",
                "Any Clinchers",
                "2+ Clinchers",
                "Series Clinchers",
                "Championship Clinchers",
            ],
            key=_KEY_CLINCH,
        )
    with filter_cols[3]:
        date_range = st.slider(
            "Date Range",
            min_value=MIN_DATE,
            max_value=max_date_bound,
            value=(MIN_DATE, max_date_bound),
            format="YYYY-MM-DD",
            key=_KEY_DATE,
        )
    with filter_cols[4]:
        st.markdown("<div style='margin-top: 1.7em'></div>", unsafe_allow_html=True)
        st.button("↺ Reset", on_click=_reset_filters, use_container_width=True)

    # Treat full-range as unfiltered
    min_date = date_range[0] if date_range[0] != MIN_DATE else None
    max_date = date_range[1] if date_range[1] != max_date_bound else None

    # ── Load & prepare data ────────────────────────────────────────────────
    df = load_location_game_days(
        db_path=db_path,
        location_group_id=location,
        playoffs_filter=playoffs_filter,
        clinch_filter=clinch_filter,
        min_date=min_date,
        max_date=max_date,
    )

    st.caption(f"Rows: {len(df)}")

    if df.empty:
        st.info("No results for the selected filters.")
    else:
        # ── Build display dataframe ────────────────────────────────────────
        df_view = df[
            [
                "date",
                "location_group_name",
                "location_group_id",
                "winners",
                "teams_playing",
                "leagues_playing",
                "sweep_status",
                "has_playoff_games",
                "series_clinching_wins",
                "championship_clinching_wins",
            ]
        ].copy()

        # Date → yyyy-mm-dd string (strip timestamp)
        df_view["date"] = pd.to_datetime(df_view["date"]).dt.strftime("%Y-%m-%d")

        # Sweep as boolean for checkbox column
        df_view["sweep"] = df_view["sweep_status"] == "Sweep"

        column_config = {
            "date": st.column_config.TextColumn("Date"),
            "location_group_name": st.column_config.TextColumn("Location"),
            "winners": st.column_config.NumberColumn("Wins", format="%d"),
            "teams_playing": st.column_config.NumberColumn("Teams", format="%d"),
            "leagues_playing": st.column_config.NumberColumn("Leagues", format="%d"),
            "sweep": st.column_config.CheckboxColumn("Sweep", disabled=True),
            "has_playoff_games": st.column_config.CheckboxColumn(
                "Playoffs", disabled=True
            ),
            "series_clinching_wins": st.column_config.NumberColumn(
                "Series Clinch", format="%d"
            ),
            "championship_clinching_wins": st.column_config.NumberColumn(
                "Champ Clinch", format="%d"
            ),
        }

        selection = st.dataframe(
            df_view.drop(columns=["location_group_id", "sweep_status"]),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config=column_config,
        )

        # ── Day Detail ─────────────────────────────────────────────────────
        st.divider()
        st.subheader("Day Detail")

        if not (selection and selection.selection and selection.selection.get("rows")):
            st.info("Select a row to see game details")
        else:
            i = selection.selection["rows"][0]
            chosen = df.iloc[i]
            chosen_day = str(chosen["date"])
            chosen_loc = str(chosen["location_group_id"])
            chosen_name = str(chosen["location_group_name"])

            st.caption(f"{chosen_day} — {chosen_name}")
            games = load_game_days(db_path, chosen_day, chosen_loc)

            if games.empty:
                st.info(
                    "No games found for that date/location (or results not populated)."
                )
            else:
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

    # ── 3+ Win Leaderboard & Charts ──────────────────────────────────────
    st.divider()

    lb_col, chart_col = st.columns(2)

    with lb_col:
        header_col, toggle_col = st.columns([3, 1], vertical_alignment="center")
        with header_col:
            st.subheader("3+ Win Leaderboard")
        with toggle_col:
            sweeps_only = st.checkbox("Only Sweeps")

        st.caption("All teams, all time")

        # Leaderboard uses unfiltered data — independent of main table filters
        lb_df = load_all_location_game_days(db_path)
        if sweeps_only:
            lb_df = lb_df[lb_df["sweep_status"] == "Sweep"]

        leaderboard = (
            lb_df.groupby("location_group_name")
            .size()
            .reset_index(name="Count")
            .sort_values("Count", ascending=False)
            .reset_index(drop=True)
        )
        leaderboard.index += 1
        leaderboard.index.name = "Rank"
        leaderboard.rename(columns={"location_group_name": "Location"}, inplace=True)

        if leaderboard.empty:
            st.info("No results for the current filters.")
        else:
            st.dataframe(leaderboard, use_container_width=True)

    with chart_col:
        st.subheader("3+ Win Day Frequency")

        # Load datasets
        year_df = load_instances_by_year(db_path)
        year_month_df = load_instances_by_year_month(db_path)
        cal_day_df = load_instances_by_calendar_day(db_path)

        if year_df.empty:
            st.info("No data available.")
        else:
            # Shared year range slider
            min_year = int(year_df["year"].min())
            max_year = int(year_df["year"].max())

            year_range = st.slider(
                "Year Range",
                min_value=min_year,
                max_value=max_year,
                value=(min_year, max_year),
            )

            # Filter all datasets by selected year range
            filtered_year_df = year_df[
                (year_df["year"] >= year_range[0]) & (year_df["year"] <= year_range[1])
            ]

            filtered_ym_df = year_month_df[
                (year_month_df["year"] >= year_range[0])
                & (year_month_df["year"] <= year_range[1])
            ]

            filtered_cal_df = cal_day_df[
                (cal_day_df["year"] >= year_range[0])
                & (cal_day_df["year"] <= year_range[1])
            ]

            tab_year, tab_month, tab_calendar = st.tabs(
                ["By Year", "By Month", "By Day"]
            )

            # ── By Year: bar chart ─────────────────────────────────────────
            with tab_year:
                bar_chart = (
                    alt.Chart(filtered_year_df)
                    .mark_bar()
                    .encode(
                        x=alt.X("year:O", title="Year"),
                        y=alt.Y(
                            "instances:Q",
                            title="Instances",
                            scale=alt.Scale(domainMin=0),
                        ),
                    )
                )
                st.altair_chart(bar_chart, use_container_width=True)

            # ── By Month: 4×3 grid ────────────────────────────────────────
            with tab_month:
                # Aggregate across years → one value per month
                month_agg = (
                    filtered_ym_df.groupby("month")["instances"].sum().reset_index()
                )
                # Ensure all 12 months present
                all_months = pd.DataFrame({"month": range(1, 13)})
                month_agg = (
                    all_months.merge(month_agg, on="month", how="left")
                    .fillna(0)
                    .astype({"instances": int})
                )
                month_agg["month_name"] = month_agg["month"].map(
                    lambda m: MONTH_LABELS[m - 1]
                )
                # 4 columns × 3 rows  (Jan–Apr | May–Aug | Sep–Dec)
                month_agg["col"] = (month_agg["month"] - 1) % 4
                month_agg["row"] = (month_agg["month"] - 1) // 4

                max_instances = int(month_agg["instances"].max()) or 1
                threshold = max_instances / 2

                base = alt.Chart(month_agg).encode(
                    x=alt.X("col:O", axis=None),
                    y=alt.Y("row:O", axis=None),
                )
                rects = base.mark_rect(
                    stroke="white", strokeWidth=3, cornerRadius=6
                ).encode(
                    color=alt.Color(
                        "instances:Q",
                        title="Instances",
                        scale=alt.Scale(scheme="blues"),
                    ),
                    tooltip=[
                        alt.Tooltip("month_name:N", title="Month"),
                        alt.Tooltip("instances:Q", title="Instances"),
                    ],
                )
                month_labels = base.mark_text(
                    fontSize=14, fontWeight="bold", dy=-8
                ).encode(
                    text="month_name:N",
                    color=alt.condition(
                        f"datum.instances > {threshold}",
                        alt.value("white"),
                        alt.value("#333"),
                    ),
                )
                count_labels = base.mark_text(fontSize=12, dy=10).encode(
                    text="instances:Q",
                    color=alt.condition(
                        f"datum.instances > {threshold}",
                        alt.value("white"),
                        alt.value("#555"),
                    ),
                )

                grid = (
                    (rects + month_labels + count_labels)
                    .properties(height=260)
                    .configure_view(strokeWidth=0)
                )
                st.altair_chart(grid, use_container_width=True)

            # ── Calendar: day-of-month × month heatmap ─────────────────────
            with tab_calendar:
                # Aggregate across years → one value per month/day
                cal_agg = (
                    filtered_cal_df.groupby(["month", "day"])["instances"]
                    .sum()
                    .reset_index()
                )
                cal_agg["month_name"] = cal_agg["month"].map(
                    lambda m: MONTH_LABELS[m - 1]
                )

                heatmap = (
                    alt.Chart(cal_agg)
                    .mark_rect(stroke="white", strokeWidth=0.5)
                    .encode(
                        x=alt.X("day:O", title="Day of Month"),
                        y=alt.Y(
                            "month_name:O",
                            title=None,
                            sort=MONTH_LABELS,
                        ),
                        color=alt.Color(
                            "instances:Q",
                            title="Instances",
                            scale=alt.Scale(scheme="blues"),
                        ),
                        tooltip=[
                            alt.Tooltip("month_name:N", title="Month"),
                            alt.Tooltip("day:O", title="Day"),
                            alt.Tooltip("instances:Q", title="Instances"),
                        ],
                    )
                    .properties(height=340)
                )
                st.altair_chart(heatmap, use_container_width=True)


if __name__ == "__main__":
    main()