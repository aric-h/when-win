from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd

import streamlit as st
from altair_theme import get_theme_colors

from fhi_scoring import Weights, compute_day_scores, compute_summary

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "local_data" / "whenwin.duckdb"
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"

# Earliest season in the dataset
MIN_SEASON = 1978

# Best/worst table size
TOP_N = 10


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
def load_teams(db_path: str) -> pd.DataFrame:
    con = get_con(db_path)
    return con.execute(_read_sql("teams_list")).df()


@st.cache_data(ttl=60)
def load_presets(db_path: str) -> dict[str, list[str]]:
    """Load preset groups from team_groups / team_group_members."""
    con = get_con(db_path)
    df = con.execute(
        "SELECT g.group_id, g.description, m.team_id "
        "FROM team_groups g "
        "JOIN team_group_members m ON m.group_id = g.group_id "
        "ORDER BY g.description, m.team_id"
    ).df()
    presets: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        label = row["description"]
        presets.setdefault(label, []).append(row["team_id"])
    return presets


@st.cache_data(ttl=60)
def load_team_group_game_days(
    db_path: str, team_ids: list[str], min_season: int, max_season: int
) -> pd.DataFrame:
    con = get_con(db_path)
    sql = _read_sql("team_group_game_days")
    return con.execute(sql, [team_ids, min_season, max_season]).df()


# ── Page ───────────────────────────────────────────────────────────────────


def main() -> None:
    # Reserve a slot for the title — filled in after scoring (or with default)
    title_slot = st.empty()
    title_slot.title("Fan Happiness Index")

    st.markdown(
        "Track the emotional trajectory of your fandom over time. "
        "Select a group of teams — the index scores each game day based on "
        "wins and losses, weighted by significance (regular season, playoffs, "
        "championships), with bonus multipliers when multiple teams win or "
        "lose together on the same day. "
        "Tune the weights in the sidebar to match your gut."
    )

    db_path = get_db_path()
    if not Path(db_path).exists():
        st.error(f"DuckDB file not found: {db_path}")
        st.stop()

    # ── Read active theme colors ───────────────────────────────────────────
    colors = get_theme_colors()

    # ── Load reference data ────────────────────────────────────────────────
    teams_df = load_teams(db_path)
    presets = load_presets(db_path)

    # Build display labels: "City TeamName (LEAGUE)"
    teams_df["label"] = (
        teams_df["city"] + " " + teams_df["team_name"] + " (" + teams_df["league"] + ")"
    )
    team_id_to_label = dict(zip(teams_df["team_id"], teams_df["label"]))
    label_to_team_id = dict(zip(teams_df["label"], teams_df["team_id"]))
    all_labels = teams_df["label"].tolist()

    # Determine max season from DB
    max_season = int(teams_df["end_year"].max()) if not teams_df.empty else date.today().year

    # ── Sidebar ────────────────────────────────────────────────────────────
    with st.sidebar:
        # ── 1. Team multiselect + preset buttons ───────────────────────────
        st.subheader("Team Group")

        # Preset buttons
        if presets:
            preset_cols = st.columns(len(presets))
            for i, (label, team_ids) in enumerate(presets.items()):
                with preset_cols[i]:
                    if st.button(label, use_container_width=True):
                        st.session_state["fhi_team_selection"] = [
                            team_id_to_label[tid]
                            for tid in team_ids
                            if tid in team_id_to_label
                        ]

        selected_labels = st.multiselect(
            "Select teams",
            options=all_labels,
            default=None,
            key="fhi_team_selection",
            placeholder="Choose teams...",
        )

        # Soft warning at 5+ teams
        if len(selected_labels) > 5:
            st.warning(
                f"{len(selected_labels)} teams selected — results work best "
                "with 5 or fewer teams."
            )

        selected_team_ids = [label_to_team_id[l] for l in selected_labels]

        # ── 2. Timeframe slider ────────────────────────────────────────────
        st.divider()
        season_range = st.slider(
            "Fandom timeframe (seasons)",
            min_value=MIN_SEASON,
            max_value=max_season,
            value=(MIN_SEASON, max_season),
            help=(
                "A season counts under its end year — "
                "e.g. the 1989–90 season is '1990'."
            ),
        )

        # ── 3. Algorithm tuning expander ───────────────────────────────────
        st.divider()
        with st.expander("⚙️ Tune the algorithm", expanded=False):
            st.markdown("**Base weights**")
            w_reg_win = st.number_input(
                "Regular season win",
                value=1.0,
                step=0.5,
                key="w_reg_win",
                help="Points added for each regular season win by a group team.",
            )
            w_reg_loss = st.number_input(
                "Regular season loss",
                value=-1.0,
                step=0.5,
                key="w_reg_loss",
                help="Points added (negative) for each regular season loss.",
            )
            w_reg_tie = st.number_input(
                "Regular season tie",
                value=0.5,
                step=0.5,
                key="w_reg_tie",
                help="Points for a regular season tie (mainly historical NFL).",
            )
            w_post_win = st.number_input(
                "Postseason win",
                value=3.0,
                step=0.5,
                key="w_post_win",
                help="Points for a postseason win (any round, not series-clinching).",
            )
            w_post_loss = st.number_input(
                "Postseason loss",
                value=-3.0,
                step=0.5,
                key="w_post_loss",
                help="Points for a postseason loss (any round, not elimination).",
            )
            w_clinch_win = st.number_input(
                "Postseason series-clinching win",
                value=5.0,
                step=0.5,
                key="w_clinch_win",
                help="Points for winning a series-clinching game (advancing to next round).",
            )
            w_clinch_loss = st.number_input(
                "Postseason season-ending loss",
                value=-5.0,
                step=0.5,
                key="w_clinch_loss",
                help="Points for a season-ending elimination loss.",
            )
            w_champ_win = st.number_input(
                "Championship win",
                value=10.0,
                step=0.5,
                key="w_champ_win",
                help=(
                    "Points for winning the championship "
                    "(Super Bowl, World Series, NBA Finals, Stanley Cup)."
                ),
            )
            w_champ_loss = st.number_input(
                "Championship loss",
                value=-7.0,
                step=0.5,
                key="w_champ_loss",
                help="Points for losing in the championship round (runner-up).",
            )

            st.markdown("**Multiplier constants**")
            sweep_growth = st.number_input(
                "Sweep growth (full sweep multiplier)",
                value=0.25,
                step=0.05,
                format="%.2f",
                key="sweep_growth",
                help=(
                    "Controls the multiplier when ALL teams playing that day "
                    "win (or all lose). Formula: 1.5 + sweep_growth × (N − 2). "
                    "Examples with default 0.25: "
                    "2-team sweep → 1.5×, "
                    "3-team sweep → 1.75×, "
                    "4-team sweep → 2.0×."
                ),
            )
            majority_growth = st.number_input(
                "Majority growth (majority agreement multiplier)",
                value=0.30,
                step=0.05,
                format="%.2f",
                key="majority_growth",
                help=(
                    "Controls the multiplier when a majority (but not all) of the "
                    "day's teams agree (all win or all lose). "
                    "Formula: 1 + majority_growth × (K − 1), where K = agreeing teams. "
                    "Examples with default 0.30: "
                    "2-of-3 agree → 1.3×, "
                    "3-of-4 agree → 1.6×."
                ),
            )

    # ── Main content area ──────────────────────────────────────────────────
    if not selected_team_ids:
        st.info("👈 Select teams in the sidebar to get started.")
        st.stop()

    st.caption(
        f"{len(selected_team_ids)} team(s) selected · "
        f"Seasons {season_range[0]}–{season_range[1]}"
    )

    # ── Load game data (cached; only re-fetched when teams/seasons change)
    games_df = load_team_group_game_days(
        db_path, selected_team_ids, season_range[0], season_range[1]
    )

    if games_df.empty:
        st.warning("No games found for the selected teams and timeframe.")
        st.stop()

    # ── Build weights from sidebar values (no DB hit on weight change) ─────
    weights = Weights(
        reg_win=w_reg_win,
        reg_loss=w_reg_loss,
        reg_tie=w_reg_tie,
        post_win=w_post_win,
        post_loss=w_post_loss,
        clinch_win=w_clinch_win,
        clinch_loss=w_clinch_loss,
        champ_win=w_champ_win,
        champ_loss=w_champ_loss,
        sweep_growth=sweep_growth,
        majority_growth=majority_growth,
    )

    # ── Score ──────────────────────────────────────────────────────────────
    day_df = compute_day_scores(games_df, weights)
    summary = compute_summary(day_df, weights)

    # ── Dynamic title: Happiness when ≥ 0, Misery when < 0 ────────────────
    title_word = "Happiness" if summary.total_index >= 0 else "Misery"
    title_slot.title(f"Fan {title_word} Index")

    # ── a. Hero total index figure ─────────────────────────────────────────
    hero_color = "green" if summary.total_index >= 0 else "red"
    st.markdown(
        f'<h1 style="color: {hero_color}; font-size: 3.5rem; '
        f'margin-bottom: 0;">{summary.total_index:+,.1f}</h1>',
        unsafe_allow_html=True,
    )

    # ── b. Funnel stats row ────────────────────────────────────────────────
    f1, f2, f3 = st.columns(3)

    with f1:
        st.metric("Game Days", f"{summary.total_game_days:,}")

    with f2:
        pct_3plus = (
            (summary.days_3plus_teams / summary.total_game_days * 100)
            if summary.total_game_days
            else 0.0
        )
        st.metric(
            "Days with 3+ Teams",
            f"{summary.days_3plus_teams:,}",
            delta=f"{pct_3plus:.1f}% of game days",
            delta_color="off",
        )

    with f3:
        pct_sweep = (
            (summary.sweep_days_3plus / summary.days_3plus_teams * 100)
            if summary.days_3plus_teams
            else 0.0
        )
        st.metric(
            "3+ Team Sweep Days",
            f"{summary.sweep_days_3plus:,}",
            delta=f"{pct_sweep:.1f}% of 3+ team days",
            delta_color="off",
        )

    # ── c. Cumulative index trend chart ────────────────────────────────────
    st.divider()

    if not day_df.empty:
        trend_chart = (
            alt.Chart(day_df)
            .mark_line(strokeWidth=1.5)
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("cumulative_index:Q", title="Cumulative Index"),
                tooltip=[
                    alt.Tooltip("date:T", title="Date"),
                    alt.Tooltip("cumulative_index:Q", title="Cumulative Index", format=",.1f"),
                    alt.Tooltip("day_score:Q", title="Day Score", format="+,.1f"),
                    alt.Tooltip("multiplier:Q", title="Multiplier", format=".2f"),
                ],
            )
            .properties(height=380)
        )
        st.altair_chart(trend_chart, use_container_width=True)

    # ── d. Best days / Worst days ──────────────────────────────────────────
    st.divider()
    best_col, worst_col = st.columns(2)

    # Prepare display-ready copy
    display_df = day_df.copy()
    display_df["date"] = pd.to_datetime(display_df["date"]).dt.strftime("%Y-%m-%d")

    table_columns = ["date", "day_score", "multiplier", "teams_playing_count", "winners_count", "losers_count"]
    table_config = {
        "date": st.column_config.TextColumn("Date"),
        "day_score": st.column_config.NumberColumn("Day Score", format="%+.1f"),
        "multiplier": st.column_config.NumberColumn("Multiplier", format="%.2f"),
        "teams_playing_count": st.column_config.NumberColumn("Teams", format="%d"),
        "winners_count": st.column_config.NumberColumn("Wins", format="%d"),
        "losers_count": st.column_config.NumberColumn("Losses", format="%d"),
    }

    with best_col:
        st.subheader("🟢 Best Days")
        best = (
            display_df.nlargest(TOP_N, "day_score")[table_columns]
            .reset_index(drop=True)
        )
        best.index += 1
        best.index.name = "#"
        st.dataframe(best, width="stretch", column_config=table_config)

    with worst_col:
        st.subheader("🔴 Worst Days")
        worst = (
            display_df.nsmallest(TOP_N, "day_score")[table_columns]
            .reset_index(drop=True)
        )
        worst.index += 1
        worst.index.name = "#"
        st.dataframe(worst, width="stretch", column_config=table_config)


main()
