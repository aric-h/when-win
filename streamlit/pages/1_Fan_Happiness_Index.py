from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

import streamlit as st

from fhi_scoring import Weights, compute_day_scores, compute_summary

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "local_data" / "whenwin.duckdb"
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"

# Earliest season in the dataset
MIN_SEASON = 1978


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
    st.set_page_config(page_title="Fan Happiness Index", layout="wide")
    st.title("Fan Happiness Index")

    db_path = get_db_path()
    if not Path(db_path).exists():
        st.error(f"DuckDB file not found: {db_path}")
        st.stop()

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
        # ── 1. Team multiselect + preset buttons (#42) ─────────────────────
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

        # ── 2. Timeframe slider (#38) ──────────────────────────────────────
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

        # ── 3. Algorithm tuning expander (#38) ─────────────────────────────
        st.divider()
        with st.expander("⚙️ Tune the algorithm", expanded=False):
            st.markdown("**Base weights**")
            w_reg_win = st.number_input(
                "Regular season win", value=1.0, step=0.5, key="w_reg_win"
            )
            w_reg_loss = st.number_input(
                "Regular season loss", value=-1.0, step=0.5, key="w_reg_loss"
            )
            w_reg_tie = st.number_input(
                "Regular season tie", value=0.5, step=0.5, key="w_reg_tie"
            )
            w_post_win = st.number_input(
                "Postseason win", value=3.0, step=0.5, key="w_post_win"
            )
            w_post_loss = st.number_input(
                "Postseason loss", value=-3.0, step=0.5, key="w_post_loss"
            )
            w_clinch_win = st.number_input(
                "Postseason series-clinching win", value=5.0, step=0.5, key="w_clinch_win"
            )
            w_clinch_loss = st.number_input(
                "Postseason season-ending loss", value=-5.0, step=0.5, key="w_clinch_loss"
            )
            w_champ_win = st.number_input(
                "Championship win", value=10.0, step=0.5, key="w_champ_win"
            )
            w_champ_loss = st.number_input(
                "Championship loss", value=-7.0, step=0.5, key="w_champ_loss"
            )

            st.markdown("**Multiplier constants**")
            sweep_growth = st.number_input(
                "Sweep growth (full sweep multiplier)",
                value=0.25,
                step=0.05,
                format="%.2f",
                key="sweep_growth",
            )
            majority_growth = st.number_input(
                "Majority growth (majority agreement multiplier)",
                value=0.30,
                step=0.05,
                format="%.2f",
                key="majority_growth",
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

    # TODO (#40): hero metric, funnel, chart, best/worst tables
    # For now, surface the raw outputs so they're visible during dev:
    st.metric("Fan Happiness Index", f"{summary.total_index:,.1f}")
    st.caption(
        f"{summary.total_game_days:,} game days · "
        f"{summary.days_3plus_teams:,} with 3+ teams · "
        f"{summary.sweep_days_3plus:,} sweep days"
    )


if __name__ == "__main__":
    main()
