# SQL & schema layer review

Source ticket: [#65](https://github.com/aric-h/when-win/issues/65) · Part of map [#62](https://github.com/aric-h/when-win/issues/62)

Scope: `sql/schema.sql`, `sql/basic_queries.sql`,
`sql/seed_preset_groups.sql`, all 8 files under `streamlit/sql/`.

`EXPLAIN` / `EXPLAIN ANALYZE` were run via `duckdb -readonly
local_data/whenwin.duckdb` against the real local DB (`team_games`:
480,224 rows; `teams`: 245; `team_location_groups`: 171;
`postseason_game_rounds`: 9,601) — not fabricated. Every optimization claim
below that includes a timing number was measured directly; the query
rewrite proposed for `location_game_days.sql` was verified to return
byte-identical results before/after.

## Summary

| # | Area | Severity | File:line |
|---|------|----------|-----------|
| O1 | Dead `JOIN teams` + redundant `SELECT DISTINCT` in the leaderboard/table query | High | `streamlit/sql/location_game_days.sql:2,7,14` |
| C1 | Live bad data: `start_year > end_year`, no constraint prevents it | Medium | `sql/schema.sql:1-9`; data row `nhl_ari_coyotes_1979` |
| O2 | Team filter applied as a late SEMI JOIN instead of pushed into the scan | Medium | `streamlit/sql/team_group_game_days.sql:28` |
| Y1 | Identical 12-line `dedup` CTE copy-pasted across 3 files (4 with a variant) | Medium | see below |
| C2 | Several logically-required `team_games` columns allow NULL | Low | `sql/schema.sql:12-21` |
| C3 | `teams.league` has no CHECK restricting it to the 4 leagues | Low | `sql/schema.sql:3` |
| I1 | DuckDB's cardinality estimate for `result IS NOT NULL` is off by ~3× | Info | n/a (planner statistics) |

## Security

**No SQL injection found — verified across all 8 runtime SQL files.**
Every dynamic value (`location_group_id`, `min_date`/`max_date`, `date`,
`team_ids`, `min_season`/`max_season`) is bound via `?` placeholders,
including `team_group_game_days.sql:28`'s `team_id = ANY(?)`, which is
DuckDB's native array-parameter binding (a Python `list` passed straight
through `con.execute(sql, [team_ids, ...])`), not string interpolation.
This is exactly the pattern the issue flagged as "most security-critical"
given it's fed by user-selected teams — confirmed safe.
`sql/basic_queries.sql` and `sql/seed_preset_groups.sql` are static,
hand-run scripts with no parameters at all.

## Optimization

**O1 — Dead join + redundant `DISTINCT` in `location_game_days.sql` (High).**
The `dedup` CTE (`streamlit/sql/location_game_days.sql:1-17`) does
`SELECT DISTINCT` over 9 columns including `t.team_name` (`:7`), pulled in
via `JOIN teams t ON t.team_id = tg.team_id` (`:14`). Neither `team_name`
nor anything derived from it is ever referenced again — not in the
`daily` CTE, not in the final `SELECT`. It's dead output. Separately, the
`SELECT DISTINCT` itself is provably redundant: every downstream metric in
`daily` is a `COUNT(DISTINCT team_id ...)` / `MAX`/`MIN`, which already
dedupes per-team regardless of how many raw rows feed it (e.g. doubleheader
games). Removing both the dead join and the `DISTINCT` (down to a plain
`GROUP BY` over `team_games JOIN team_location_groups`):
- Verified via `duckdb -readonly` to return **byte-identical output**
  (359 rows, diffed against the original — no difference).
- Measured `EXPLAIN ANALYZE`, 3 runs each: original **~0.21s** (0.223s /
  0.211s / 0.208s) → simplified **~0.19s** (0.192s / 0.190s / 0.199s), a
  ~10% reduction. `EXPLAIN` also confirms the original plan runs *two*
  `HASH_GROUP_BY` passes (one for the `DISTINCT`, one for the real
  aggregation) over the full 475,810-row post-join set, where the
  simplified version runs one.

  Modest at today's data size, but this query backs the main table *and*
  the leaderboard on every page load of the primary page, and the dataset
  only grows every season.

**O2 — Team filter applied late in `team_group_game_days.sql` (Medium, no
measured impact today).** `EXPLAIN` on the FHI page's query shows the
`WHERE gg.team_id = ANY(?)` predicate (`:28`) compiling to a `HASH_JOIN
(Join Type: SEMI)` applied *after* both the `winner` self-join
(`team_games` × `team_games`, filtered to `result='W'`, ~236,562 rows) and
the `postseason_game_rounds` left join — instead of being pushed down into
the first scan of `gg`. In other words, the two self-joins process a large
slice of the whole league's history before narrowing down to the 4-ish
teams a user actually selected. Measured impact today: `EXPLAIN ANALYZE`
with a 4-team Boston group over the full 1978–2026 range still completes
in **0.029s** — DuckDB's vectorized engine eats this easily at 480K rows,
so no action is urgent. Flagging for awareness: if this becomes a real
bottleneck as data/concurrency grows, wrapping `gg` in a filtering CTE
(`WITH my_games AS (SELECT * FROM team_games WHERE team_id = ANY(?) AND
result IS NOT NULL AND season BETWEEN ? AND ?)` and joining `winner`/`pgr`
off that) is the direction to try first — untested here since it's not
worth micro-optimizing a 29ms query.

**I1 — Cardinality-estimate skew on `result IS NOT NULL` (Info).**
Every `EXPLAIN` plan that includes this filter (present throughout
`streamlit/sql/`, `sql/schema.sql`'s `CHECK` doesn't actually enforce
NOT NULL — see C2) estimates it removes roughly a third of `team_games`
rows (e.g. "~152,170 rows" out of 480,224). Verified directly: **0 of
480,224 rows** have a NULL `result` today — the ingestion scripts skip
incomplete games before insert (see [#63](https://github.com/aric-h/when-win/issues/63)), so this filter is
currently a no-op. Not causing a measurable slowdown (queries are all
sub-350ms), but a 3× estimate error is worth knowing about if future
queries get more complex and lean on the optimizer's join-order choices —
DuckDB's `ANALYZE`-equivalent statistics may be worth refreshing after
ingestion if this becomes relevant.

## Constraints & schema

**C1 — Live bad data from a missing `start_year <= end_year` constraint
(Medium).** `sql/schema.sql:1-9` (`teams`) has no check relating
`start_year` and `end_year`. One row already violates the obvious
invariant: `nhl_ari_coyotes_1979` has `start_year=1979, end_year=1978`.
This isn't just cosmetic — `api_utils.resolve_team_id()`
([#63](https://github.com/aric-h/when-win/issues/63)) matches teams via
`start_year <= season AND (end_year IS NULL OR end_year >= season)`; a row
with `start_year > end_year` can never satisfy that condition for *any*
season, so this team_id is permanently unresolvable through the normal
lookup path. Worth a data-cleanup pass and a
`CHECK (end_year IS NULL OR start_year <= end_year)` to stop it recurring.

**C2 — Several logically-required `team_games` columns allow NULL (Low).**
`sql/schema.sql:12-21`: `date`, `league`, `season`, and `game_type` have
no `NOT NULL`, even though every ingestion script always populates them
(verified: 0 NULLs across all four columns, 480,224 rows). Only `game_id`
and `team_id` are marked `NOT NULL`. Currently harmless since application
code enforces the invariant, but it means the DB isn't backing up that
guarantee — worth tightening if this schema is ever written to from
somewhere other than the four ingestion scripts.

**C3 — `teams.league` has no CHECK constraint (Low).** Unlike
`team_games.game_type`, which has `CHECK (game_type IN ('regular',
'postseason'))` (`sql/schema.sql:26`), `teams.league` (`:3`) has no
equivalent restricting it to `{MLB, NBA, NFL, NHL}`. Data is clean today
(verified: exactly those 4 values), but nothing at the DB level stops a
typo from an ingestion or seed script.

## Style

**Y1 — The `dedup` CTE is copy-pasted across four files (Medium).** The
identical 12-line pattern —
```sql
WITH dedup AS (
  SELECT DISTINCT
    tg.date, tlg.location_group_id, tg.league, tg.team_id, tg.result
  FROM team_games tg
  JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
  WHERE tg.date IS NOT NULL AND tg.result IS NOT NULL
),
```
appears verbatim in `streamlit/sql/instances_by_calendar_day.sql:1-12`,
`instances_by_year.sql:1-12`, and `instances_by_year_month.sql:1-12`, and
in an extended form (see O1) in `location_game_days.sql:1-17`. All four
compute the same "3+ teams from a location won on the same day" base set,
just aggregated at different date granularities. DuckDB supports
`CREATE VIEW` / `CREATE MACRO`; either would let these four files share
one definition instead of four hand-kept copies (and, per O1, the
`DISTINCT` in that shared definition should be dropped — it's redundant
everywhere it's used, not just in `location_game_days.sql`).

**`seed_preset_groups.sql` and `basic_queries.sql`: no findings.** The
DELETE+INSERT idempotency pattern in `seed_preset_groups.sql` (fixed in a
prior commit — DuckDB requires a PK/UNIQUE target for `INSERT OR REPLACE`,
which `team_groups`/`team_group_members` didn't have) is correctly applied
throughout. `basic_queries.sql` is explicitly a scratch/example file per
its own comment — hardcoded team IDs and a fixed season are expected
there, not a defect.
