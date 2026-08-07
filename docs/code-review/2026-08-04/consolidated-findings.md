# Consolidated code review findings — WhenWin

Destination artifact for map [#62](https://github.com/aric-h/when-win/issues/62) · Synthesizes [#63](https://github.com/aric-h/when-win/issues/63), [#64](https://github.com/aric-h/when-win/issues/64), [#65](https://github.com/aric-h/when-win/issues/65), [#66](https://github.com/aric-h/when-win/issues/66)

Findings only — no fixes were applied as part of this map. Detail, code
snippets, and verification evidence (EXPLAIN plans, timing, CVE research,
git-history scan) live in the four source docs linked throughout; this doc
groups everything by what to do about it, not by which file it's in.

- [ingestion-scripts.md](ingestion-scripts.md) (#63) — 11 findings
- [streamlit-app.md](streamlit-app.md) (#64) — 10 findings
- [sql-schema.md](sql-schema.md) (#65) — 7 findings
- [dependency-hygiene.md](dependency-hygiene.md) (#66) — 4 findings

## Bottom line

**No critical or actively-exploitable security vulnerability was found.**
All four reviews specifically checked for SQL injection, secrets in git
history, XSS-adjacent risk, and credential handling — all came back clean
or with only low-severity, non-exploitable-today items. The two things
closest to "security" that are worth real attention are a version-pinning
gap (below) and a live data-integrity bug that isn't a security issue but
has a real functional impact.

## 1. Security & data-integrity — do these soonest

| Finding | Why it matters | Source |
|---|---|---|
| `requests>=2.28.0` floor predates a `.netrc`-leak CVE fix (2.32.4) | Low exploitability here (ingestion URLs aren't attacker-influenced), but a one-line `requirements.txt` fix closes a real, named gap before the app is public | [#66 D2](dependency-hygiene.md#dependency-pinning--cves) |
| `nhl_ari_coyotes_1979` has `start_year=1979 > end_year=1978` in live data, with no constraint stopping it | Not a security bug, but it means this team_id can **never** resolve via `resolve_team_id()` — a real, silent data gap | [#65 C1](sql-schema.md#constraints--schema) |
| DB connections in all 4 ingestion scripts are never explicitly closed | Under DuckDB's single-writer model, an unhandled exception mid-cron-run risks blocking the next run and Streamlit reads | [#63 S1](ingestion-scripts.md#security) |
| `--schema` CLI arg is executed as raw SQL, unvalidated | Not attacker-reachable today (ops-only), but worth closing before any less-trusted trigger path is added | [#63 S2](ingestion-scripts.md#security) |

Everything else security-related across all four docs (SQL injection
checks, XSS/`unsafe_allow_html` audit, session-state isolation, CORS/XSRF
defaults, secrets-in-history scan, Streamlit/DuckDB CVE research) came
back **verified clean** — see each doc's Security section for the
evidence rather than re-stating it here.

## 2. Quick wins — small diff, low risk, worth doing first

| Finding | Effort | Source |
|---|---|---|
| Drop the dead `JOIN teams` + redundant `SELECT DISTINCT` in `location_game_days.sql` — verified byte-identical output, ~10% faster | 1 file, already diffed and timed | [#65 O1](sql-schema.md#optimization) |
| Fix the `nhl_ari_coyotes_1979` row and add a `CHECK (end_year IS NULL OR start_year <= end_year)` | 1 data fix + 1 constraint | [#65 C1](sql-schema.md#constraints--schema) |
| Bump `requests` floor to `>=2.32.4` | 1 line | [#66 D2](dependency-hygiene.md#dependency-pinning--cves) |
| Remove the dead `wins_needed` variable and the confusing `for game_id, *_ in {...}` dedup idiom | 2 lines total | [#63 Y3, Y4](ingestion-scripts.md#style) |
| Reconcile the NBA rate-limit sleep: code uses `0.7`, `CLAUDE.md` documents `0.6` | 1 line (doc or code) | [#63 Y6](ingestion-scripts.md#style) |
| Align `1_Fan_Happiness_Index.py` on `width="stretch"` (it's the only chart still using the older `use_container_width`) | 1 line | [#64 Y3](streamlit-app.md#style) |
| Add a `.python-version` / `runtime.txt` | 1 file | [#66 D3](dependency-hygiene.md#dependency-pinning--cves) |

## 3. Worth scheduling — real duplication/reliability wins, more than a one-liner

| Finding | What it fixes | Source |
|---|---|---|
| Extract shared `streamlit/db_utils.py` (`get_db_path`, `get_con`, `read_sql`) | Removes the largest duplication in the codebase; also fixes the app opening two separate cached DuckDB connections | [#64 Y1/O1](streamlit-app.md#style) |
| Extract shared `determine_result(away, home)` helper into `api_utils.py` | Removes verbatim-duplicated win/loss/tie logic from 2 ingestion scripts | [#63 Y1](ingestion-scripts.md#style) |
| Extract the copy-pasted `dedup` CTE (3 files verbatim, 1 modified) into a DuckDB `VIEW`/`MACRO` | Same fix as the #65 quick-win DISTINCT removal, applied once instead of 4x | [#65 Y1](sql-schema.md#style) |
| Wrap all 4 ingestion `main()` bodies in `try/finally: con.close()` | Same root cause as the #1 connection-cleanup item, mechanical once the pattern is picked | [#63 S1](ingestion-scripts.md#security) |
| Collapse `load_all_location_game_days` into `load_location_game_days(..., None, "Any", "Any", None, None)` | Removes a near-duplicate query-building function | [#64 Y2](streamlit-app.md#style) |
| Add upper bounds or a lockfile for `requests`/`nba_api`/`pandas`/`streamlit` | Makes `pip install -r requirements.txt` reproducible | [#66 D1](dependency-hygiene.md#dependency-pinning--cves) |

## 4. Lower urgency — real, but not costing anything today

- **FHI scoring's `df.apply(axis=1)` hot path** — flagged right where the app's own ">5 teams" warning kicks in; worth vectorizing with `np.select`, but needs care (and ideally tests first) to preserve exact behavior. [#64 O2](streamlit-app.md#optimization)
- **`team_group_game_days.sql`'s team filter compiles to a late SEMI JOIN** instead of pushing into the scan — confirmed via `EXPLAIN`, but measured at 29ms today. Revisit if data/concurrency grows. [#65 O2](sql-schema.md#optimization)
- **Nullable columns that are always populated in practice** (`team_games.date/league/season/game_type`, `teams.league` with no CHECK) — tightening these means touching the live schema; fine to bundle with the #1 CHECK-constraint fix rather than doing separately. [#65 C2/C3](sql-schema.md#constraints--schema)
- **Row-by-row `iterrows()`** in `ingest_nba_api.py` (full-season backfill) and `load_presets()` (tiny table, negligible) — same anti-pattern, different urgency. [#63 O1](ingestion-scripts.md#optimization), [#64 O3](streamlit-app.md#optimization)
- **Redundant per-game DB round-trip** in `ingest_postseason_metadata.py`'s MLB path — low volume today. [#63 O2](ingestion-scripts.md#optimization)
- Assorted low-severity style items (two different "series winner" algorithms in one file, mid-function cross-script import, CSS on private `data-testid` selectors, widget-key naming drift, cardinality-estimate skew in DuckDB's planner) — see source docs; none block anything.

## 5. Test coverage — recommendation, not a to-do here

Zero automated tests exist anywhere in the repo. Every area review flagged
this as a finding per scope, not something to fix in this map. If/when a
testing effort starts, the highest-value, lowest-effort target is
**`streamlit/fhi_scoring.py`** — it's explicitly written with no SQL and
no Streamlit imports specifically so it *can* be tested in isolation
(`compute_day_scores`, `compute_summary`, `_assign_base_weight`,
`_compute_multiplier` are all deterministic pure functions), and yet has
no coverage today. Second priority: the pure-logic ingestion helpers
(`nhl_season_end_year`, `abbrev_to_city_name`, `decode_nba_game_id`,
`nfl_round_from_game_id`, the win/loss/tie determination logic once it's
extracted per §3 above) — same story, side-effect-free and easy to pin
down. Lowest priority, but worth a mention: the query-correctness check
this map did by hand for §2's `location_game_days.sql` rewrite (diffing
before/after output) is exactly the kind of thing a small fixture-seeded
DuckDB test suite would catch automatically instead of one-off.

## 6. Root-level note files (passing mention only)

`dev_notes.md`, `scratch.md`, and `handoff_docs/` are local
scratch/handoff-skill output, not application code, per #62's scope notes
— reviewed here only to confirm that's still true. No findings, no ticket
warranted.

## Not in scope for this map

- Writing the tests recommended in §5.
- `scripts/archived/` (27 dead files) — deliberately retained for the
  documentation/articles map ([#68](https://github.com/aric-h/when-win/issues/68)).
- Actually applying any of the fixes above — this map's destination is
  the findings doc itself.
