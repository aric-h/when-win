# Query architecture: arbitrary team-group game days

Asset for [Design query architecture for arbitrary team groups](https://github.com/aric-h/when-win/issues/30),
part of [Wayfinder Map: Fan Happiness Index page](https://github.com/aric-h/when-win/issues/22).

## Summary

New static `.sql` file, `streamlit/sql/team_group_game_days.sql`, bound with
3 params: `[team_id_list, min_date, max_date]`. Returns one row per
`(team_id, game_id)` — **not** aggregated to one row per day — with the
result, game type, postseason round, and clinch flags needed to look up a
base weight per row. All weight/multiplier/day-rollup/cumulative-index math
stays in pandas in the app layer, not in SQL.

## 1. Selecting games for an arbitrary team list

`team_location_groups` (market-anchored) doesn't apply here — the input is
a raw `team_id` list, so filter `team_games` directly:

```sql
WHERE tg.team_id = ANY(?)
```

Confirmed DuckDB's Python client binds a Python `list` to `ANY(?)` and
matches element-wise — no need to build a dynamic `IN (?, ?, ..., ?)`
placeholder string the way `location_game_days.sql` builds dynamic WHERE
fragments in Python for its *optional* filters. Team list and year range are
both mandatory inputs here (not optional toggles), so they belong directly
in the `.sql` file as `?` placeholders, matching the `game_days.sql`
pattern rather than the `location_game_days.sql` dynamic-WHERE pattern.

## 2. Year range → `season`, not calendar `date`

[Fandom timeframe model](https://github.com/aric-h/when-win/issues/24)
resolved a continuous year range but didn't specify calendar-year-of-`date`
vs. season-year semantics. Recommendation: filter on `season BETWEEN ? AND
?`, not `date`. `season` is already end-year-normalized across leagues
(`CLAUDE.md`), so it cleanly includes a season's full run regardless of
which calendar years it straddles. Filtering by raw calendar `date` year
would incorrectly split a season — e.g. a 1990 boundary would keep an
NBA/NHL season's Jan–Jun 1990 games but drop its Oct–Dec 1989 games, even
though a fan would call that whole season "1990." **Flagging as an
assumption to confirm during** [Prototype & tune the algorithm](https://github.com/aric-h/when-win/issues/31),
since the UI copy ("year range") should match whichever semantics are
chosen.

## 3. Deriving the losing team's clinch flags

`is_series_clinching` / `is_championship_clinching` are set only on the
winning team's row (`CLAUDE.md` global invariant). To score a group team's
season-ending loss (-5) or championship loss (-7), self-join `team_games`
back to itself on `game_id`, unfiltered by the team-group list — **the
winner of a group team's elimination game is very often not in the group**
(a group team usually loses *to* an outside opponent), so the join target
must be the full `team_games` table, not the `team_id = ANY(?)`-filtered
CTE:

```sql
LEFT JOIN team_games winner
  ON winner.game_id = gg.game_id
 AND winner.result   = 'W'
```

Then derive per-row:

```sql
CASE
  WHEN gg.result = 'W' THEN gg.is_series_clinching
  WHEN gg.result = 'L' THEN COALESCE(winner.is_series_clinching, FALSE)
  ELSE FALSE  -- ties never clinch
END AS series_clinching_derived
```

(same shape for `championship_clinching_derived`). No fan-out risk: `(game_id,
team_id)` is the PK, at most one row per `game_id` has `result = 'W'`, and
when `gg.result = 'W'` the join target is `gg`'s own row.

**MLB game_id format**: verified this self-join is format-agnostic. Both
rows of a game are written from the same `game_id` variable in a single
ingestion call — `scripts/ingest_mlb_api.py:198` sets
`game_id = f"mlb_{game_pk}"` once and reuses it for both team rows; the
Retrosheet-format historical loader does the same. The two MLB formats
(`mlb_2019_...` vs `mlb_<gamePk>`) only matter to code that *parses*
structure out of the string — an equality self-join on `game_id` doesn't
care which format it is, since both sides of the same game always carry
the identical string.

## 4. Row grain: `(team_id, game_id)`, not `(team_id, date)`

MLB doubleheaders mean a team can have two `game_id`s on the same `date`.
Aggregating to one row per `(team_id, date)` at the SQL layer (the way
`location_game_days.sql`'s `daily` CTE does via `COUNT(DISTINCT team_id)`)
would silently collapse or misrepresent split doubleheader results. Keep
the query at game grain; day-level rollup (needed for the sweep
multiplier) happens in pandas, where doubleheader handling can be a
deliberate, visible choice rather than baked into an opaque `GROUP BY`.
**Flagging doubleheader day-rollup semantics as unresolved — for**
[Prototype & tune the algorithm](https://github.com/aric-h/when-win/issues/31)
**to decide** (e.g. does a split doubleheader count as a "sweep" day?).

## 5. New `.sql` file vs. Python-built query

New static file: `streamlit/sql/team_group_game_days.sql`.

```sql
SELECT
  gg.date,
  gg.game_id,
  gg.league,
  gg.season,
  gg.team_id,
  gg.opponent_team_id,
  gg.result,
  gg.game_type,
  pgr.round_order AS playoff_round_order,
  pgr.round_name  AS playoff_round,
  CASE
    WHEN gg.result = 'W' THEN COALESCE(gg.is_series_clinching, FALSE)
    WHEN gg.result = 'L' THEN COALESCE(winner.is_series_clinching, FALSE)
    ELSE FALSE
  END AS series_clinching_derived,
  CASE
    WHEN gg.result = 'W' THEN COALESCE(gg.is_championship_clinching, FALSE)
    WHEN gg.result = 'L' THEN COALESCE(winner.is_championship_clinching, FALSE)
    ELSE FALSE
  END AS championship_clinching_derived
FROM team_games gg
LEFT JOIN team_games winner
  ON winner.game_id = gg.game_id
 AND winner.result   = 'W'
LEFT JOIN postseason_game_rounds pgr
  ON pgr.league = gg.league AND pgr.game_id = gg.game_id
WHERE gg.team_id = ANY(?)
  AND gg.result IS NOT NULL
  AND gg.season BETWEEN ? AND ?
ORDER BY gg.date, gg.team_id
```

Loader in `streamlit/app.py`, mirroring `load_game_days()` / the
`_read_sql()` + `con.execute(sql, params).df()` pattern:

```python
@st.cache_data(ttl=60)
def load_team_group_game_days(
    db_path: str,
    team_ids: list[str],
    min_season: int,
    max_season: int,
) -> pd.DataFrame:
    con = get_con(db_path)
    sql = _read_sql("team_group_game_days")
    return con.execute(sql, [team_ids, min_season, max_season]).df()
```

**Why not bake weights/multipliers/day-rollup into this query** (the way
`location_game_days.sql` bakes in `sweep_status`): base weights and
multipliers are sidebar-tunable ([Index algorithm and base weights](https://github.com/aric-h/when-win/issues/26)).
Cache key for this loader is only `(team_ids, min_season, max_season)` —
stable while a user drags a weight slider. If weight math lived in SQL,
every slider tweak would need a fresh `st.cache_data` key and a DB
round-trip. Keeping this query's job to "shape the raw per-team-per-game
facts" and doing weight lookup + day-level sweep detection + cumulative
index in pandas means slider changes are pure in-memory recomputation over
an already-loaded dataframe.

## Downstream: what pandas does with this dataframe

Not this ticket's job to finalize (belongs to [Prototype & tune the algorithm](https://github.com/aric-h/when-win/issues/31)),
but the shape this query enables:

1. Row-level: map each `(game_type, result, series_clinching_derived,
   championship_clinching_derived)` combo to a base weight via the table
   from [Index algorithm and base weights](https://github.com/aric-h/when-win/issues/26).
2. Day-level: `groupby("date")` over the loaded dataframe to detect sweep
   scenarios (2-team, 3-team, 2-of-3) per [Index algorithm and base weights](https://github.com/aric-h/when-win/issues/26),
   applying the multiplier to that day's summed base weights.
3. Cumulative: running sum of the (weighted, multiplied) daily score across
   the sorted date range, per [Cumulative trend chart](https://github.com/aric-h/when-win/issues/29).
