# Ingestion & shared scripts review

Source ticket: [#63](https://github.com/aric-h/when-win/issues/63) · Part of map [#62](https://github.com/aric-h/when-win/issues/62)

Scope: `scripts/api_utils.py`, `scripts/ingest_nhl_api.py`,
`scripts/ingest_nba_api.py`, `scripts/ingest_mlb_api.py`,
`scripts/ingest_postseason_metadata.py`. `scripts/archived/` is
intentionally excluded (retained for the articles map).

Evaluated against the near-term assumption that the app becomes
public-facing (nightly cron running against a shared, single-writer
DuckDB file).

## Summary

| # | Area | Severity | File:line |
|---|------|----------|-----------|
| S1 | DB connections never explicitly closed | Medium | `api_utils.py:14`, all `ingest_*.py` `main()` |
| O1 | Row-by-row pandas iteration over full season game logs | Medium | `ingest_nba_api.py:126` |
| O2 | Per-game DB round-trip inside a loop instead of one batched query | Medium | `ingest_postseason_metadata.py:614-617` |
| Y1 | Win/loss/tie logic duplicated verbatim across two scripts | Medium | `ingest_nhl_api.py:145-150`, `ingest_mlb_api.py:191-196` |
| Y2 | Two different "series winner" algorithms in the same file | Low | `ingest_postseason_metadata.py:114`, `:443` |
| Y3 | Dead variable | Low | `ingest_postseason_metadata.py:98` |
| Y4 | Convoluted dedup idiom | Low | `ingest_postseason_metadata.py:337` |
| Y5 | Mid-function cross-script import | Low | `ingest_postseason_metadata.py:651-654` |
| S2 | `schema_path` arg executed as raw SQL with no validation | Low | `api_utils.py:14-17` |
| Y6 | Doc/code mismatch on NBA rate-limit sleep | Info | `ingest_nba_api.py:119` vs `CLAUDE.md` |
| T1 | No automated tests for parsing/transform logic | — | recommendation only |

## Security

**S1 — DB connections never explicitly closed (Medium).**
`api_utils.connect()` returns a raw `duckdb.connect()` handle; none of the
four `main()` functions wrap their work in `try/finally` or use the
connection as a context manager, so a mid-run exception (a malformed API
response, an unresolved team, a network timeout) leaves the process to exit
without an explicit `con.close()`. Under this project's single-writer
DuckDB model (`CLAUDE.md`: "only one process can write at a time"), a nightly
cron job that dies mid-write and doesn't release its lock promptly can block
both Streamlit reads and the next cron run. Fix direction: wrap each
script's body in `try/finally: con.close()`.

**S2 — `schema_path` executed as raw SQL, unvalidated (Low).**
`connect()` (`api_utils.py:14-17`) does `con.execute(Path(schema_path).read_text(...))`
against a path that's a CLI arg (`--schema`, default `sql/schema.sql`). Not
attacker-reachable today — these are ops-run scripts, no external/remote
input feeds `--schema` — but worth a note for defense-in-depth if any of
these scripts ever get wired into an automated or less-trusted trigger
path.

**No SQL injection found.** Every query across all four files uses
parameterized `?` placeholders (including the dynamic `IN (?, ?)` /
`UNNEST(?)` patterns in `ingest_postseason_metadata.py`); no string-built
SQL was found in the ingestion layer.

**No secrets/credentials found.** All three league APIs are public,
unauthenticated endpoints — nothing to leak here.

## Optimization

**O1 — Row-by-row pandas iteration (Medium).**
`ingest_nba_api.py:126`: `for _, row in df.iterrows(): rows.append({...})`
runs once per game-row per season per season-type. `iterrows()` is
pandas' slowest row-access pattern; for a full historical backfill (48
seasons × 2 season types) this is a real, avoidable cost. Prefer
`df.to_dict("records")` or `itertuples()`.

**O2 — Redundant per-game DB round-trip (Medium).**
`ingest_postseason_metadata.py:614-617`, inside `process_mlb`:
```python
dates_in_series = [
    con.execute("SELECT date FROM team_games WHERE game_id=? LIMIT 1", [g["game_id"]]).fetchone()[0]
    for g in games
]
```
issues one query per game in the series instead of a single
`WHERE game_id IN (...)` batched query. This is exactly the "redundant DB
round-trip" pattern #63 asks to flag — low volume today (a handful of
games per series), but avoidable.

**Informational — full historical backfill is fully sequential.**
`ingest_nhl_api.py` and `ingest_mlb_api.py` walk their date range in fixed
7-day / 30-day chunks with a blocking `time.sleep()` between requests, and
`ingest_nba_api.py` does the same per season. Fine for nightly deltas (the
whole point of `latest_result_date()`); a from-scratch 1978–present backfill
would be thousands of sequential blocking calls and could take hours. No
action needed unless a full rebuild becomes a routine operation.

## Style

**Y1 — Win/loss/tie logic duplicated verbatim (Medium).**
Identical 6-line blocks in `ingest_nhl_api.py:145-150` and
`ingest_mlb_api.py:191-196`:
```python
if away_score > home_score:
    away_res, home_res = "W", "L"
elif home_score > away_score:
    away_res, home_res = "L", "W"
else:
    away_res = home_res = "T"
```
Worth extracting into `api_utils.py` as e.g.
`determine_result(away_score, home_score) -> tuple[str, str]`, alongside
the other shared helpers already there (`resolve_team_id`, `upsert_games`).
(`ingest_nba_api.py` doesn't need this — the NBA API already returns `WL`
per team.)

**Y2 — Two different series-winner algorithms in one file (Low).**
`reconstruct_from_game_rounds` (`ingest_postseason_metadata.py:114`) picks
the winner via `max(wins.values())`; `process_nba`'s 2026+ path
(`:443`) picks the winner via `wins >= NBA_WINS_TO_CLINCH` (a fixed
threshold of 4). Both are correct for a *completed* best-of-7 series, but
having two different concepts for "who won the series" in the same module
is a maintainability trap — a future best-of-5 or play-in edge case could
make them diverge silently.

**Y3 — Dead variable (Low).** `ingest_postseason_metadata.py:98`:
`wins_needed = {"NHL": 4, "NBA": 4, "MLB": None}` is assigned inside
`reconstruct_from_game_rounds` and never read anywhere in the function.

**Y4 — Convoluted dedup idiom (Low).** `ingest_postseason_metadata.py:337`:
```python
for game_id, *_ in {(r[0],) for r in game_rows}:
```
builds a set of 1-tuples just to unpack and discard the empty remainder.
Equivalent to, and clearer as: `for game_id in {r[0] for r in game_rows}:`.

**Y5 — Mid-function cross-script import (Low).**
`ingest_postseason_metadata.py:651-654` (`_mlb_api_id_map`) does
`from ingest_mlb_api import TEAM_ID_MAP` and `from api_utils import
resolve_team_id` inside the function body rather than at module level.
This only works because both scripts live in the same directory (added to
`sys.path` when run directly) — an implicit coupling between two ingest
scripts that isn't obvious from either file's top-level imports. Moving
`TEAM_ID_MAP` to `api_utils.py` (or importing at module scope) would make
the dependency explicit.

**Y6 — Doc/code mismatch on NBA rate-limit sleep (Info).** `CLAUDE.md`
says "Add `time.sleep(0.6)` between calls to avoid 429s"; the actual code
(`ingest_nba_api.py:119`) sleeps `0.7`. More conservative than documented,
so not a bug — but the two should be reconciled so they don't drift
further apart.

## Cross-cutting note (not acted on here)

**T1 — No automated tests.** None of the pure-logic helpers in this area
(`nhl_season_end_year`, `api_team_to_city_name`, `abbrev_to_city_name`,
`decode_nba_game_id`, `nfl_round_from_game_id`, the win/loss/tie
determination) have unit tests, despite being straightforward,
side-effect-free functions that are exactly the kind of thing worth
covering. Recorded here as a finding only, per #62/#63 scope — writing
tests is out of scope for this map. Recommendation is rolled up in the
consolidated doc ([#67](https://github.com/aric-h/when-win/issues/67)).
