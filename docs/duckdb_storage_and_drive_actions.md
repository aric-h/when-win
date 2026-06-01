# DuckDB storage policy and Google Drive + GitHub Actions pattern

## Purpose

This document defines the storage policy for the canonical `whenwin.duckdb` file and the recommended operational pattern for downloading, updating, validating, and re-uploading it from GitHub Actions.

This repo **does not commit the DuckDB binary**. The repo stores code, SQL, app code, and operational docs; Google Drive stores the canonical database artifact.

## Canonical production location

- **Shared Drive path:** `when-win/data/duckdb/prod/whenwin.duckdb`
- **Current Google Drive file ID:** `184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`
- **Current role of this file:** canonical production DuckDB used for ingestion/app workflows

Why this location:

- it is in a **Shared Drive**, not a personal Drive root
- it gives the team a stable, discoverable location
- it keeps the production DB separate from repo contents
- it supports a single-writer operational model

## Storage policy

### 1. The DuckDB binary is not committed to GitHub

The production `.duckdb` file must remain outside the Git repository.

Reasons:

- the file is already large and will continue to grow
- Git is a poor fit for frequently changing binary databases
- binary churn makes history noisy and expensive
- the file should be managed as an operational artifact, not source code

### 2. The Shared Drive file is the source of truth for the production DB

The canonical production DB lives at:

`when-win/data/duckdb/prod/whenwin.duckdb`

Local copies on laptops or CI runners are **working copies only**.

### 3. Use a single-writer model for the production DB

The production DB should be updated by **one automation path at a time**.

Recommended rule:

- GitHub Actions job = normal writer for scheduled ingestion/update runs
- humans may download/read the DB as needed
- humans should avoid manually overwriting the prod DB except for explicitly coordinated recovery operations

This reduces the risk of:

- stale uploads overwriting fresher data
- ambiguous “latest” copies
- accidental divergence between team members

### 4. Prefer folder-level sharing; avoid ad hoc file sharing

Access should be granted through the Shared Drive / project folder structure, not by repeatedly sharing individual files.

Recommended access pattern:

- storage/integration maintainers: edit/manage access
- engineers/analysts: view/download access unless they truly need write access
- avoid public link sharing for the DB file

If available, use Google Groups to manage membership rather than adding individuals one by one.

### 5. Keep the prod filename stable

The production filename should remain:

`whenwin.duckdb`

A stable name and location simplify automation and reduce ambiguity.

### 6. Use separate locations for prod, staging, and archive

Recommended Shared Drive structure:

```text
when-win/
  data/
    duckdb/
      prod/
        whenwin.duckdb
      staging/
        whenwin_staging.duckdb
      archive/
        whenwin_YYYY-MM-DD.duckdb
```

Current confirmed production location already follows this pattern at the `prod/` level.

Recommended next step:

- create `staging/` for non-prod testing
- create `archive/` for dated snapshots / rollback points

### 7. Never rely on a personal Drive root as canonical storage

The team should not treat a user’s personal Google Drive root as the production DB home. Shared Drive placement is the correct convention for this project.

## Recommended GitHub Actions operating pattern

The recommended workflow is:

1. checkout repo
2. authenticate to Google Drive
3. download the current prod DB to the runner
4. run ingestion / update scripts against the local DB copy
5. run validation / sanity checks
6. upload the validated DB back to the canonical prod file location
7. optionally write a dated archive snapshot

### High-level flow

```text
Google Drive prod DB
  -> download to runner temp path
  -> run ingestion scripts locally
  -> validate DB
  -> replace/update prod DB in Drive
  -> optionally save archive snapshot
```

## Recommended automation rules

### Idempotency

Ingestion logic should remain idempotent:

- rerunning the same job for the same date should not create duplicates
- scripts should use deterministic keys and/or upsert/replace logic
- a failed run should be safe to retry

### Concurrency control

Only one workflow should update the production DB at a time.

Recommended GitHub Actions setting:

- use a workflow `concurrency` group such as `whenwin-duckdb-prod`
- disable overlapping scheduled runs

### Validation before upload

Before re-uploading the DB, run a minimal validation gate. At minimum:

- confirm the DB file exists and is readable
- confirm expected tables are present
- run league-specific or global sanity checks
- confirm file size is within a sane expected range

Do **not** upload a DB that fails validation.

### Archive snapshot after successful upload

Recommended but optional:

- after a successful prod update, also write a dated snapshot to `archive/`
- use names like `whenwin_2026-06-01.duckdb` or `whenwin_2026-06-01T173000Z.duckdb`

This gives the team a rollback point if a later job corrupts or unexpectedly changes the DB.

### Retention policy

Suggested starting retention:

- keep daily snapshots for 14 to 30 days
- if needed later, keep weekly or monthly long-term snapshots

## Configuration recommendations for CI

Do not hard-code Google Drive identifiers in Python source.

Recommended GitHub configuration:

### Repository variables / secrets

Use repo variables or environment-specific variables for non-secret identifiers:

- `GDRIVE_DUCKDB_FILE_ID=184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`
- optionally `GDRIVE_DUCKDB_PROD_PATH=when-win/data/duckdb/prod/whenwin.duckdb`

Use secrets for credentials/tokens only.

### Authentication guidance

This project currently has Google Workspace access authenticated through a user account. That may be acceptable for initial manual/team operations, but for CI the preferred long-term pattern is:

- a non-personal automation identity if available, or
- a carefully managed OAuth/token-based integration stored in GitHub Secrets

Avoid embedding credentials in the repo or in the DB management scripts.

## Recommended implementation shape in GitHub Actions

A production ingestion workflow should follow this structure:

```yaml
name: update-prod-duckdb

on:
  schedule:
    - cron: '15 10 * * *'
  workflow_dispatch:

concurrency:
  group: whenwin-duckdb-prod
  cancel-in-progress: false

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install -r requirements.txt

      - name: Authenticate to Google Drive
        run: |
          # integration-specific auth step goes here
          # credentials should come from GitHub Secrets

      - name: Download prod DuckDB from Google Drive
        run: |
          # download file ID -> local temp path, e.g. data/whenwin.duckdb

      - name: Run ingestion updates
        run: |
          # run one or more ingestion scripts against local DB copy

      - name: Run sanity checks
        run: |
          # fail fast if validation does not pass

      - name: Upload validated DB back to Drive
        run: |
          # replace canonical prod file with validated local DB

      - name: Write archive snapshot
        if: success()
        run: |
          # optional: create dated copy in archive folder
```

## File handling recommendations for scripts/workflows

### Download target

Use a local runner path such as:

- `data/whenwin.duckdb`, or
- `${{ runner.temp }}/whenwin.duckdb`

Then pass that path into scripts using an env var or CLI argument.

### Atomic local workflow pattern

Recommended local pattern:

1. download prod DB to a temp path
2. copy or move into the working location
3. run updates
4. validate
5. upload only after success

This avoids partial/half-written local states becoming the upload candidate.

### Avoid duplicate “current” files in Drive

Do not create ad hoc variants like:

- `whenwin latest.duckdb`
- `whenwin final.duckdb`
- `whenwin (1).duckdb`

There should be one canonical prod filename and, if desired, clearly dated archive copies.

## Operational recovery guidance

If an ingest/upload fails:

- do not manually upload a laptop copy unless the team has confirmed it is the correct recovery artifact
- prefer restoring from the most recent successful archive snapshot
- note the failed run in the PR/issue/logs and document the recovery action taken

## Current implementation assumptions

This document assumes:

1. the Shared Drive location `when-win/data/duckdb/prod/whenwin.duckdb` is the correct canonical prod location
2. the current Drive file ID `184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi` remains stable unless the file is replaced in a way that generates a new Drive object
3. GitHub Actions authentication to Google Drive will be wired separately from this doc change
4. ingestion code will continue to follow the repo convention that the DuckDB binary is external to Git

## Follow-up recommendations

Recommended next actions after this doc lands:

1. add `staging/` and `archive/` folders under `when-win/data/duckdb/`
2. choose and document the CI authentication method for Google Drive
3. add a small helper module or workflow step for:
   - Drive download by file ID
   - upload/replace prod DB
   - archive snapshot creation
4. add workflow-level concurrency protection before enabling scheduled prod writes
5. add or standardize a `WHENWIN_DB` environment variable for all ingestion scripts and the Streamlit app

## Summary

- the production DB belongs in the Shared Drive, not in Git and not in a personal Drive root
- `when-win/data/duckdb/prod/whenwin.duckdb` is the canonical prod path
- GitHub Actions should download -> update locally -> validate -> re-upload -> optionally archive
- production updates should use a single-writer, non-overlapping workflow pattern
