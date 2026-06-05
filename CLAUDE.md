# WhenWin — CLAUDE.md

## Foundational Guidelines

- In all interactions and commit messages, be extremely concise and sacrifice
  grammar for the sake of concision.
- At the end of each plan, give me a list of unresolved questions to answer, if any.
  Make the questions extremely concise.

## Motivational Intent

Find and display every day since 1978 where **3 or more teams from the same geographic
market each won a game across 3 or more of the Big 4 leagues** (MLB, NBA, NFL, NHL).
The "modern post-merger era" baseline is the 1978 season for all four leagues.

Secondary goals: fan happiness index (postseason wins weighted by round/clinch significance),
and a custom team-picker page for arbitrary cross-market comparisons.

---

## Non-Obvious Tooling

| Tool | Purpose |
|------|---------|
| `duckdb` CLI | Installed system-wide; use `duckdb local_data/whenwin.duckdb` for ad-hoc queries. Add `-readonly` if Streamlit is running. |
| `.venv/` | Project venv in repo root. Always activate before running scripts: `source .venv/bin/activate`. Packages: `duckdb`, `requests`, `nba_api`. |
| NHL API | `https://api-web.nhle.com/v1/` — official, free, no auth. Key endpoints: `/schedule/{date}`, `/playoff-bracket/{season}`. |
| NBA API | `nba_api` Python package wrapping `stats.nba.com`. Add `time.sleep(0.6)` between calls to avoid 429s. |
| MLB API | `https://statsapi.mlb.com/api/v1/` — official, free, no auth. Key endpoint: `/schedule?sportId=1&...`. |
| NFL | No official free API. Game data ingested from CSV/GitHub sources. NFL season runs Sept–Feb; no automation gap until next season. |

---

## Architectural Map

```text
local_data/whenwin.duckdb   ← NOT in git (~85MB, growing)
sql/schema.sql              ← source of truth for all table definitions
scripts/
  api_utils.py              ← shared: connect(), latest_result_date(), resolve_team_id(), upsert_games()
  ingest_nhl_api.py         ← NHL schedule API → team_games
  ingest_nba_api.py         ← nba_api (stats.nba.com) → team_games
  ingest_mlb_api.py         ← MLB Stats API → team_games
  ingest_postseason_metadata.py  ← all leagues → postseason_series, postseason_game_rounds, is_series_clinching
streamlit/
  app.py                    ← local UI (Streamlit); read_only DB connection
  requirements.txt          ← pip deps for UI only
```

**Nightly refresh order** (run from repo root with venv active):

```bash
python scripts/ingest_nhl_api.py
python scripts/ingest_nba_api.py
python scripts/ingest_mlb_api.py
python scripts/ingest_postseason_metadata.py
```

Each script auto-detects the latest result date and only fetches forward. All are idempotent.

---

## Key Schema Facts

- `team_games`: **2 rows per game** — one per team. Primary key is `(game_id, team_id)`.
- `season` column = **end year** of the season. The 2025–26 NBA season → `season = 2026`.
- `game_type` is either `'regular'` or `'postseason'` — no other values.
- `result` is `'W'`, `'L'`, or `'T'` — never NULL for completed games.
- `is_series_clinching`: set on the winning team's row only for the game that ended a series.
- `is_championship_clinching`: set on the winning team's row only. Championship clinchers are always also series clinchers.
- `team_location_groups`: maps each `team_id` to a `location_group_id`. This is the join used for every geo-based query. Teams that moved cities have distinct `team_id` rows with different `start_year`/`end_year`.

---

## Rules & Verifiable Instructions

1. **Schema changes**: Always edit `sql/schema.sql` AND apply `ALTER TABLE` to the live DB. The schema file is used by ingestion scripts on startup (`CREATE TABLE IF NOT EXISTS`).
2. **Running scripts**: Always run from the repo root (`/Users/aric/code/when-win`), not from inside `scripts/`. Relative paths in scripts assume this.
3. **Checking data**: Verify with `duckdb -readonly local_data/whenwin.duckdb "SELECT ..."` when Streamlit is running (only one writer allowed at a time).
4. **Adding a team**: Insert into `teams`, add to `team_location_groups`, and if applicable update `franchises`. All three tables must be consistent.
5. **Postseason metadata**: After any postseason game ingestion, run `ingest_postseason_metadata.py` to keep `postseason_game_rounds`, `postseason_series`, and `is_series_clinching` current.

---

## Hard Constraints & Anti-Patterns

- **Never commit `local_data/whenwin.duckdb`** — it's in `.gitignore` and too large for git.
- **DuckDB single-writer**: only one process can write at a time. Streamlit connects `read_only=True`; ingestion scripts must not run while another writer is open.
- **Do not use `postseason_series` for clinch detection in queries** — the old approach joined against that table to infer `is_series_clinching`. That caused fan-out. Use `team_games.is_series_clinching` directly.
- **Do not invent game_ids** — they must come from the ingestion source. Old MLB IDs use Retrosheet format; new ones use `mlb_<gamePk>`. NBA IDs from 2026 onward use `nba_004XXXXXXXX`; older use `nba_4XXXXXXX` (8 digits). Don't mix.
- **NFL series table**: NFL is single-elimination; `postseason_series` is not populated for NFL (each game is its own implicit series). Don't query `postseason_series` expecting NFL rows.

---

## Gotchas & Tribal Knowledge

- **MLB has two game_id formats**: Retrosheet-style (`mlb_2019_2019-10-04_TBA_HOU_1_1`) for historical data, and `mlb_<gamePk>` for API-ingested games (2026 postseason onward). Queries that parse game_ids must handle both.
- **NBA game_id encodes round** (2026+ only): `nba_004YYRSGG` — character at position 12 (1-indexed) is the playoff round number (1–4).
- **NHL Mighty Ducks era**: API returns `"Ducks"` as `commonName` even for 1997–2006 seasons when they were `"Mighty Ducks"`. The team resolver uses normalized name matching (strips spaces) to handle `"Black Hawks"` vs `"Blackhawks"`. A few 2003 Anaheim series entries in `postseason_series` are missing — minor cosmetic gap, doesn't affect `is_series_clinching`.
- **NBA 2022–2025 postseason has 21 series** (not 15): the Play-In Tournament games are stored as `game_type='postseason'` and get reconstructed as additional series entries.
- **MLB 2026 schedule skeleton was deleted**: An accidental ingest of the 2026 schedule CSV left 3,746 NULL-score rows. These were purged. New MLB data uses the Stats API.
- **`season` for NHL**: the NHL API returns `season=20252026`; extract end year with `season % 10000`.
- **Streamlit `@st.cache_resource`** is used for the DB connection (shared across reruns); `@st.cache_data(ttl=60)` for query results. If data looks stale in the UI, trigger a rerun or restart Streamlit.

---

## Pointers to Deeper Docs

- `sql/schema.sql` — all table definitions with constraints
- `sql/basic_queries.sql` — starter analytical queries
- `docs/` — DuckDB storage policy and S3/Drive migration planning notes
- `dev_notes.md` — historical run commands and early architecture notes (partially stale)
- NHL API reference: https://api-web.nhle.com/v1/ (no official docs; community reference at https://github.com/Zmalski/NHL-API-Reference)
- MLB Stats API: https://statsapi.mlb.com/api/v1/ — append `?fields=...` to any endpoint for field discovery
- nba_api package docs: https://github.com/swar/nba_api
