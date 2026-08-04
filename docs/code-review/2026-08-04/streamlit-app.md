# Streamlit application review

Source ticket: [#64](https://github.com/aric-h/when-win/issues/64) · Part of map [#62](https://github.com/aric-h/when-win/issues/62)

Scope: `streamlit/app.py`, `streamlit/win_occurrences.py`,
`streamlit/fhi_scoring.py`, `streamlit/altair_theme.py`,
`streamlit/pages/1_Fan_Happiness_Index.py`,
`streamlit/.streamlit/config.toml`.

Evaluated against public traffic. `config.toml`'s `[server]`-level settings
(CORS, XSRF) are out of scope here — that's explicitly [#66](https://github.com/aric-h/when-win/issues/66)'s
territory; this doc only covers the theme values `altair_theme.py` reads
from it.

## Summary

| # | Area | Severity | File:line |
|---|------|----------|-----------|
| Y1 | DB-access boilerplate duplicated verbatim across both page scripts | High | `win_occurrences.py:40-56`, `1_Fan_Happiness_Index.py:30-46` |
| O1 | Duplicated `get_con()` per page → two separate cached DuckDB connections instead of one | Medium | same as Y1 |
| Y2 | `load_all_location_game_days` reimplements `load_location_game_days` instead of calling it unfiltered | Medium | `win_occurrences.py:116-123` |
| O2 | `df.apply(..., axis=1)` over full game history in the scoring hot path | Medium | `fhi_scoring.py:73` |
| Y3 | Mixed Altair sizing API: `use_container_width` (old) vs `width="stretch"` (new) | Low | `1_Fan_Happiness_Index.py:514` |
| S1 | `unsafe_allow_html` f-string template for the hero index figure | Low | `1_Fan_Happiness_Index.py:350-354` |
| Y4 | Widget-key convention differs between the two page scripts | Low | `1_Fan_Happiness_Index.py:137,177,211...` |
| Y5 | CSS targets private `data-testid` selectors | Low | `1_Fan_Happiness_Index.py:358-372` |
| O3 | `iterrows()` on preset-groups load | Low | `1_Fan_Happiness_Index.py:66` |
| T1 | No tests for the pure-pandas FHI scoring engine | — | recommendation only |

## Security

**No SQL injection found.** Every widget value that reaches SQL goes
through parameterized `?` binding — `location_group_id`, `min_date`,
`max_date` in `win_occurrences.py`'s `load_location_game_days`, and
`selected_team_ids` / `season_range` in the FHI page's
`load_team_group_game_days`. The `playoffs_filter` / `clinch_filter`
selectboxes never reach SQL as raw text either — their values are compared
with Python `==` against a fixed set of literal strings, and only a
corresponding *static* SQL fragment is appended (`win_occurrences.py:91-105`);
the widget value itself is never interpolated into the query string. Team
selection in the FHI sidebar is similarly indirect: the multiselect can
only return values from `options=filtered_labels` (built from the DB's own
team list), which are then dict-mapped back to `team_id`s before being
passed as a parameterized array — not typed free text. Deeper SQL-side
verification (the actual `team_id = ANY(?)` construction) belongs to
[#65](https://github.com/aric-h/when-win/issues/65).

**S1 — `unsafe_allow_html` sites (Low, none currently exploitable).**
Three sites use `unsafe_allow_html=True`:
- `win_occurrences.py:225` — a static spacer `<div>`, no interpolation.
- `1_Fan_Happiness_Index.py:358-372` — a static `<style>` block, no
  interpolation.
- `1_Fan_Happiness_Index.py:350-354` — an f-string template:
  `f'<h1 style="color: {hero_color}; ...">{summary.total_index:+,.1f}</h1>'`.
  `hero_color` is one of two hardcoded literals (`"green"`/`"red"`) and
  `summary.total_index` is a computed float — neither is user-controlled
  text today, so there's no live XSS. Flagging it anyway because it's the
  one site in the codebase where an f-string is interpolated directly into
  raw HTML; if this pattern gets reused for anything that carries
  user-influenced text (a custom label, a team name join), it becomes an
  injection point. Prefer `st.metric`/`st.markdown` without
  `unsafe_allow_html`, or keep interpolated values strictly numeric.

**No session-state leakage found.** `st.session_state` is per-browser-session
by Streamlit design. The two caches that *are* process-wide —
`@st.cache_resource` (the DuckDB connection) and `@st.cache_data(ttl=60)`
(query results, keyed by their full argument tuple) — only ever hold
read-only query results keyed by the exact filter/team/season values that
produced them, so two users with the same filters correctly see the same
cached (public) data; there's no per-user state being cross-served.

## Optimization

**O1 — Duplicated `get_con()` doubles the connection count (Medium).**
Because `win_occurrences.py` and `1_Fan_Happiness_Index.py` each define
their own `@st.cache_resource def get_con(db_path)`, Streamlit treats them
as two distinct cached resources — the app opens **two** separate
`duckdb.connect(..., read_only=True)` handles instead of one shared
connection. Not a correctness bug (DuckDB allows multiple readers), but an
avoidable doubling of open file handles that a shared module (see Y1)
would eliminate for free.

**O2 — Row-wise `.apply()` in the FHI scoring hot path (Medium).**
`fhi_scoring.py:73`: `df["base_weight"] = df.apply(_assign_base_weight,
axis=1, w=w)` runs a Python-level function call per row on every score
recompute (any weight-slider tweak triggers a full rerun). The app itself
warns at >5 selected teams ("results work best with 5 or fewer") — that
warning threshold is exactly where this cost starts to bite. `_assign_base_weight`'s
priority-order logic (`fhi_scoring.py:39-67`) is a straightforward
if/elif chain over a handful of boolean/categorical columns and is a good
candidate for vectorizing with `np.select` instead of `.apply(axis=1)`.

**O3 — `iterrows()` on preset-groups load (Low).**
`1_Fan_Happiness_Index.py:66`: `for _, row in df.iterrows():` over the
`team_groups`/`team_group_members` join. Same anti-pattern flagged in the
ingestion review ([#63](https://github.com/aric-h/when-win/issues/63)) —
here the table is tiny (a handful of preset groups), so impact is
negligible, but noting the recurring pattern.

## Style

**Y1 — DB-access boilerplate duplicated across both page scripts (High).**
`win_occurrences.py:1-56` and `1_Fan_Happiness_Index.py:1-46` each
independently define `DEFAULT_DB_PATH`, `SQL_DIR`, `_read_sql()`,
`get_db_path()`, and `get_con()` — near-identical code (the only
differences are the `Path(__file__).resolve().parents[N]` depth, adjusted
for each file's location). This is the clearest reuse opportunity in the
Streamlit layer: extract a shared `streamlit/db_utils.py` exposing
`get_db_path(default_path)`, `get_con(db_path)`, and a `read_sql(sql_dir,
name)` that takes the SQL directory as a parameter. Fixes O1 as a
byproduct.

**Y2 — `load_all_location_game_days` reimplements the filtered loader (Medium).**
`win_occurrences.py:116-123` rebuilds the same `WITH q AS (...) SELECT *
FROM q ORDER BY ...` pattern as `load_location_game_days` (`:65-113`)
just to run it with no filters for the leaderboard. Calling
`load_location_game_days(db_path, None, "Any", "Any", None, None)`
instead (all filters neutral) would return the same result without a
second near-duplicate function and cache entry.

**Y3 — Mixed Altair chart-sizing API (Low).** Every chart in
`win_occurrences.py` uses `width="stretch"` (e.g. `:441`, `:507`, `:544`),
but `1_Fan_Happiness_Index.py:514` uses the older `use_container_width=True`
on the same `st.altair_chart` call. Streamlit has been moving away from
`use_container_width` in favor of `width`; worth aligning both pages on
the same parameter before it's deprecated further.

**Y4 — Widget-key convention differs between page scripts (Low).**
`win_occurrences.py` defines module-level `_KEY_LOCATION` /
`_KEY_PLAYOFFS` / `_KEY_CLINCH` / `_KEY_DATE` constants for widget `key=`
values (used consistently in `_reset_filters()` too). `1_Fan_Happiness_Index.py`
uses raw string literals inline instead (`"fhi_preset_select"`,
`"fhi_team_selection"`, `"w_reg_win"`, `"sweep_growth"`, etc. — 11+
occurrences). Not wrong, just inconsistent; the FHI page doesn't have a
`_reset_filters()`-style function that would benefit from named constants
today, but the inconsistency makes the two pages read like they were
written to different conventions.

**Y5 — CSS targets private Streamlit internals (Low).**
`1_Fan_Happiness_Index.py:358-372` injects a `<style>` block selecting
`[data-testid="stMetric"]`, `[data-testid="stMetricLabel"]`, etc. to
center metric text. `data-testid` attributes are Streamlit's internal DOM
hooks, not a documented/stable public API — an unannounced Streamlit
version bump could silently stop this styling from applying. No action
needed now beyond awareness; if this pattern spreads, consider isolating
such selectors in one shared constant so a future breakage is a one-place
fix.

**Theme fallback values verified consistent.** `altair_theme.py`'s
`_FALLBACK` dict (`:13-26`) matches `.streamlit/config.toml`'s
`[theme.dark]` / `[theme.light]` values exactly (hex-for-hex) — if these
ever drift, the Altair charts would silently mismatch the app chrome. No
finding, just confirmed correct today.

**`st.navigation` / `st.Page` architecture compliance verified.** Neither
page script (`win_occurrences.py`, `pages/1_Fan_Happiness_Index.py`) calls
`st.set_page_config`, matching the `CLAUDE.md` hard constraint that only
`app.py` may call it.

## Cross-cutting note (not acted on here)

**T1 — No tests for the FHI scoring engine.** `fhi_scoring.py` is
explicitly designed to be pure and Streamlit-free ("No SQL, no Streamlit
imports" — its own docstring), which makes it the single most
test-friendly module in the repo, and yet it has zero coverage:
`_assign_base_weight`'s priority ordering, `_compute_multiplier`'s
sweep/majority thresholds, and `compute_summary`'s aggregation math are
all deterministic pure functions that unit tests could pin down exactly.
Recorded as a finding only, per #62/#64 scope — recommendation rolled up
in [#67](https://github.com/aric-h/when-win/issues/67).
