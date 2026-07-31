# WhenWin

Discovering the best days in sports fandom.

WhenWin finds and displays every day since 1978 (the "modern post-merger
era" for all four major North American leagues) where **3 or more teams
from the same geographic market each won a game**, across **3 or more of
the Big 4 leagues** (MLB, NBA, NFL, NHL). A secondary Fan Happiness Index
page scores an arbitrary team group's fandom timeline, weighting postseason
wins by round and clinch significance.

Built on DuckDB + Streamlit.

## Status

Local-only v1. A hosted, public version is in the works — for now, running
it locally is the only way to try it.

## Running it locally

```bash
git clone https://github.com/aric-h/when-win.git
cd when-win
python -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt
pip install -r streamlit/requirements.txt
```

Populate the local DuckDB file (`local_data/whenwin.duckdb`, not checked
into git) by running the ingestion scripts from the repo root, in order:

```bash
python scripts/ingest_nhl_api.py
python scripts/ingest_nba_api.py
python scripts/ingest_mlb_api.py
python scripts/ingest_postseason_metadata.py
```

**Heads up on historical data**: these scripts hit each league's live API,
which only goes back so far (NHL/NBA/MLB APIs don't serve games from the
late '70s/'80s). The full 1978–present dataset was originally backfilled
from other sources — Retrosheet (MLB), Hockey-Reference (NHL),
Basketball-Reference / Kaggle (NBA), and CSV/GitHub sources (NFL). Those
one-off import scripts are retired but kept for reference in
[`scripts/archived/`](scripts/archived/) if you want to reconstruct a full
historical dataset yourself.

Once the DB is populated, run the app:

```bash
streamlit run streamlit/app.py
```

## Architecture

See [`CLAUDE.md`](CLAUDE.md) for the full architectural map — schema
facts, ingestion pipeline, gotchas, and file-by-file pointers. It's written
as agent-onboarding context, but it's also the most complete developer
guide to the codebase.

Additional design notes live under [`docs/`](docs/).

## Contributing

Issues and PRs are welcome — there's no formal contribution process yet,
so just open one.

## License

MIT — see [`LICENSE`](LICENSE).
