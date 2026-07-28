"""Fan Happiness Index — pure-pandas scoring engine.

No SQL, no Streamlit imports.  Takes the raw game-level dataframe from
``load_team_group_game_days()`` and the 11 sidebar weight/multiplier
values, returns day-level scores and summary stats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


# ── Dataclass for weight/multiplier bundle ─────────────────────────────────


@dataclass(frozen=True)
class Weights:
    """All tuneable parameters for FHI scoring."""

    reg_win: float = 1.0
    reg_loss: float = -1.0
    reg_tie: float = 0.5
    post_win: float = 3.0
    post_loss: float = -3.0
    clinch_win: float = 5.0
    clinch_loss: float = -5.0
    champ_win: float = 10.0
    champ_loss: float = -7.0
    sweep_growth: float = 0.25
    majority_growth: float = 0.30


# ── 1. Row-level base weight ───────────────────────────────────────────────


def _assign_base_weight(row: pd.Series, w: Weights) -> float:
    """Return the base weight for a single game row.

    Priority order (first match wins):
      championship clinch > series clinch > postseason > regular season
    """
    result = row["result"]
    is_champ = bool(row.get("championship_clinching_derived", False))
    is_clinch = bool(row.get("series_clinching_derived", False))
    is_post = row.get("game_type") == "postseason"

    if is_champ and result == "W":
        return w.champ_win
    if is_champ and result == "L":
        return w.champ_loss
    if is_clinch and result == "W":
        return w.clinch_win
    if is_clinch and result == "L":
        return w.clinch_loss
    if is_post and result == "W":
        return w.post_win
    if is_post and result == "L":
        return w.post_loss
    # Regular season
    if result == "W":
        return w.reg_win
    if result == "L":
        return w.reg_loss
    return w.reg_tie  # ties


def assign_base_weights(df: pd.DataFrame, w: Weights) -> pd.DataFrame:
    """Add a ``base_weight`` column to the game-level dataframe."""
    df = df.copy()
    df["base_weight"] = df.apply(_assign_base_weight, axis=1, w=w)
    return df


# ── 2. Per-team-per-date rollup (doubleheader handling) ────────────────────


def _team_date_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate to one row per (date, team_id).

    Returns columns:
      date, team_id, net_weight, wins, losses, games,
      is_split (True when a team has both W and L on same date)
    """
    grouped = df.groupby(["date", "team_id"]).agg(
        net_weight=("base_weight", "sum"),
        wins=("result", lambda s: (s == "W").sum()),
        losses=("result", lambda s: (s == "L").sum()),
        games=("game_id", "nunique"),
    ).reset_index()

    # A split doubleheader: same team has ≥1 W and ≥1 L on the same date
    grouped["is_split"] = (grouped["wins"] > 0) & (grouped["losses"] > 0)
    return grouped


# ── 3. Sweep / majority multiplier ────────────────────────────────────────


def _compute_multiplier(
    team_day: pd.DataFrame, w: Weights
) -> tuple[float, int, int, int]:
    """Compute the day multiplier from per-team-per-date rows for ONE date.

    Returns (multiplier, teams_playing, winners, losers).
    """
    teams_playing = len(team_day)

    # For sweep/majority: exclude split-doubleheader teams from tally
    tally = team_day[~team_day["is_split"]]
    n = len(tally)

    # Count clean winners / losers in the tally
    clean_winners = int((tally["wins"] > 0).sum()) if n else 0
    clean_losers = int((tally["losses"] > 0).sum()) if n else 0

    # K = max agreement count
    k = max(clean_winners, clean_losers)

    if n >= 2 and k == n:
        # Full sweep — all tally teams agree
        multiplier = 1.5 + w.sweep_growth * (n - 2)
    elif n >= 3 and k >= math.ceil(n / 2) and k < n:
        # Majority but not full sweep
        multiplier = 1.0 + w.majority_growth * (k - 1)
    else:
        multiplier = 1.0

    return multiplier, teams_playing, clean_winners, clean_losers


# ── 4. Day-level scoring ──────────────────────────────────────────────────


def compute_day_scores(df: pd.DataFrame, w: Weights) -> pd.DataFrame:
    """Full pipeline: game rows → day-level scored dataframe.

    Input: raw dataframe from ``load_team_group_game_days()``.
    Output: one row per date with columns:
      date, day_score, cumulative_index, teams_playing_count,
      winners_count, losers_count, multiplier, raw_weight_sum
    """
    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "day_score",
                "cumulative_index",
                "teams_playing_count",
                "winners_count",
                "losers_count",
                "multiplier",
                "raw_weight_sum",
            ]
        )

    # Step 1 — base weights
    df = assign_base_weights(df, w)

    # Step 2 — per-team-per-date rollup
    team_day = _team_date_rollup(df)

    # Step 3 — per-date aggregation
    records: list[dict] = []
    for day, group in team_day.groupby("date"):
        raw_sum = group["net_weight"].sum()
        mult, n_playing, n_win, n_loss = _compute_multiplier(group, w)
        records.append(
            {
                "date": day,
                "raw_weight_sum": raw_sum,
                "multiplier": mult,
                "day_score": raw_sum * mult,
                "teams_playing_count": n_playing,
                "winners_count": n_win,
                "losers_count": n_loss,
            }
        )

    day_df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)

    # Step 4 — cumulative index
    day_df["cumulative_index"] = day_df["day_score"].cumsum()

    return day_df


# ── 5. Summary stats ──────────────────────────────────────────────────────


@dataclass
class FHISummary:
    """High-level stats derived from the day-level dataframe."""

    total_index: float
    total_game_days: int
    total_games: int

    # 1-team day stats
    days_1_team: int
    wins_on_1_team_days: int
    games_on_1_team_days: int

    # 2-team day stats
    days_2_teams: int
    sweep_wins_2_teams: int
    sweep_losses_2_teams: int

    # 3+ team day stats
    days_3plus_teams: int
    sweep_wins_3plus: int
    sweep_losses_3plus: int


def compute_summary(
    day_df: pd.DataFrame, w: Weights, total_games: int = 0
) -> FHISummary:
    """Derive summary statistics from the day-level dataframe.

    Parameters
    ----------
    day_df : pd.DataFrame
        Output of ``compute_day_scores()``.
    w : Weights
        Scoring weights (used for sweep multiplier threshold).
    total_games : int
        Total individual game rows (from the raw games dataframe).
    """
    if day_df.empty:
        return FHISummary(
            total_index=0.0,
            total_game_days=0,
            total_games=0,
            days_1_team=0,
            wins_on_1_team_days=0,
            games_on_1_team_days=0,
            days_2_teams=0,
            sweep_wins_2_teams=0,
            sweep_losses_2_teams=0,
            days_3plus_teams=0,
            sweep_wins_3plus=0,
            sweep_losses_3plus=0,
        )

    # ── Subsets by team count ──────────────────────────────────────────────
    one_team = day_df[day_df["teams_playing_count"] == 1]
    two_team = day_df[day_df["teams_playing_count"] == 2]
    three_plus = day_df[day_df["teams_playing_count"] >= 3]

    # ── 1-team day stats ──────────────────────────────────────────────────
    days_1_team = len(one_team)
    wins_on_1_team_days = int(one_team["winners_count"].sum()) if days_1_team else 0
    # games_on_1_team_days: normally equals days_1_team but can differ with
    # doubleheaders (teams_playing_count is distinct teams, not games)
    games_on_1_team_days = days_1_team  # 1 team → 1 game per day in tally

    # ── 2-team day sweep stats ────────────────────────────────────────────
    # A sweep on a 2-team day means both teams agree (multiplier ≥ 1.5).
    # Win sweep: winners_count == 2; Loss sweep: losers_count == 2.
    sweep_2 = two_team[two_team["multiplier"] >= 1.5]
    sweep_wins_2 = int((sweep_2["winners_count"] == 2).sum())
    sweep_losses_2 = int((sweep_2["losers_count"] == 2).sum())

    # ── 3+ team day sweep stats ───────────────────────────────────────────
    sweep_3plus = three_plus[three_plus["multiplier"] >= 1.5]
    sweep_wins_3plus = int(
        (sweep_3plus["winners_count"] == sweep_3plus["teams_playing_count"]).sum()
    )
    sweep_losses_3plus = int(
        (sweep_3plus["losers_count"] == sweep_3plus["teams_playing_count"]).sum()
    )

    return FHISummary(
        total_index=float(day_df["cumulative_index"].iloc[-1]),
        total_game_days=len(day_df),
        total_games=total_games,
        days_1_team=days_1_team,
        wins_on_1_team_days=wins_on_1_team_days,
        games_on_1_team_days=games_on_1_team_days,
        days_2_teams=len(two_team),
        sweep_wins_2_teams=sweep_wins_2,
        sweep_losses_2_teams=sweep_losses_2,
        days_3plus_teams=len(three_plus),
        sweep_wins_3plus=sweep_wins_3plus,
        sweep_losses_3plus=sweep_losses_3plus,
    )
