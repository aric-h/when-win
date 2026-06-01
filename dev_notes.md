# whenwin dev notes

Local sports synchronization analytics on DuckDB.

## Current slice

- League-agnostic schema in `sql/schema.sql`
- NFL CSV ingestion script: `scripts/ingest_nfl_csv.py`
- Team ID migration script: `scripts/migrate_team_ids.py`
- NFL reference metadata/helpers: `scripts/nfl_reference.py`
- Historical teams importer: `scripts/import_teams_csv.py`
- City prefix override config: `config/team_id_city_prefix_overrides.csv`
- NBA Kaggle ingestion: `scripts/ingest_nba_kaggle.py`
- NBA 1947-present gap-fill: `scripts/ingest_nba_1947_present.py`
- NHL Hockey-Reference ingestion: `scripts/ingest_nhl_hockey_reference.py`
- NHL teams CSV builder: `scripts/build_nhl_teams_csv.py` -> `raw/nhl/hockey-reference/nhl_teams.csv`
- MLB teams CSV builder: `scripts/build_mlb_teams_csv.py` -> `raw/mlb/mlb_teams.csv`
- Integrity checks: `scripts/sanity_checks.py`
- Starter analytical queries: `sql/basic_queries.sql`

## Run

```bash
source .venv/bin/activate
python scripts/migrate_team_ids.py --dry-run
python scripts/migrate_team_ids.py
python scripts/import_teams_csv.py --csv raw/nfl/nfl_teams.csv
python scripts/ingest_nfl_csv.py --csv raw/nfl/2025.csv --season 2025 --replace-season
python scripts/sanity_checks.py --league NFL --season 2025
python - <<'PY'
import duckdb
con = duckdb.connect('data/whenwin.duckdb')
sql = open('sql/basic_queries.sql', 'r', encoding='utf-8').read()
for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
    print('---')
    print(con.execute(stmt).fetchall())
PY
```

## Streamlit (local UI)

The repo includes a simple local Streamlit app in `streamlit_app/app.py`.

```bash
python -m pip install -r streamlit_app/requirements.txt
streamlit run streamlit_app/app.py
```

To point at a different DB file:

```bash
WHENWIN_DB=/path/to/whenwin.duckdb streamlit run streamlit_app/app.py
```

## Notes

- `team_games` stores one row per team per game.
- Each real game yields exactly two rows.
- `game_id` is deterministic: `nfl_<season>_<week-token>_<away>_<home>`.
- Team IDs follow `<league>_<city-prefix>_<mascot>` (example: `nfl_min_vikings`).
- `teams.franchise_id` groups historical/current team identities under a root franchise.
