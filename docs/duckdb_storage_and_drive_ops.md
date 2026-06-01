# DuckDB Storage Policy and Google Drive Operations

## Purpose

This document defines how the `when-win` project stores, shares, and updates the canonical DuckDB database artifact. The DuckDB binary is an operational data artifact, not source code, so it must not be committed to GitHub. Google Drive management and integration for this artifact is owned by Jake.

## Current canonical production artifact

- **Shared Drive path:** `when-win/data/duckdb/prod/whenwin.duckdb`
- **Current Google Drive link:** `https://drive.google.com/file/d/184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi/view?usp=drive_link`
- **Role of this file:** canonical production DuckDB artifact used for local analysis, ingestion validation, and downstream app reads

This file is intentionally stored outside GitHub because DuckDB binaries are large, mutable, and unsuitable for versioning in the application repository.

## Storage policy

### 1) Keep the DuckDB binary out of GitHub

The repository should include:

- ingestion scripts
- SQL schema and migration files
- analytical Python modules and query logic
- documentation and runbooks

The repository must not include:

- `*.duckdb`
- ad hoc database snapshots
- large binary exports used as operational state

### 2) Use Shared Drive as the system of record for the binary artifact

The DuckDB binary should live in the shared project Drive structure, not in any individual user's My Drive and not in Drive root. The current project path is appropriate:

```text
when-win/
  data/
    duckdb/
      prod/
        whenwin.duckdb
```

This keeps team ownership separate from personal storage and makes access management operationally cleaner.

### 3) Treat the production file as a managed artifact, not a collaborative live database

Google Drive is being used as artifact storage and distribution, not as a multi-user database server.

That means:

- do not expect multiple users to write to the same synced `.duckdb` file safely
- do not run parallel write workflows against the same Drive-hosted production file
- do not treat Google Drive sync semantics as database locking or transaction control

Recommended operating model:

- one controlled writer process updates the database
- users who need local access download a copy or use a synced local copy for read-only work
- production updates are performed in a controlled workflow and then re-uploaded

### 4) Use least-privilege access

Recommended access pattern:

- **Editors:** only the small set of maintainers or automation identities that refresh or replace the production DB
- **Viewers:** teammates who only need to download and inspect the file
- **No public link sharing** unless there is a specific business need and explicit approval

If automation is introduced, prefer a dedicated automation identity over a personal account token.

### 5) Maintain snapshots outside the prod path

The current `prod/whenwin.duckdb` path should represent the latest promoted production database. As the system matures, add a parallel snapshots path such as:

```text
when-win/data/duckdb/snapshots/
```

Recommended file naming:

```text
whenwin_YYYY-MM-DD_HHMMUTC.duckdb
```

Examples:

- `whenwin_2026-06-01_1715UTC.duckdb`
- `whenwin_2026-06-08_0100UTC.duckdb`

This makes rollback and auditability much easier than relying on opaque Drive revision history alone.

## Recommended Google Drive + GitHub Actions pattern

## Goals

A GitHub Actions workflow should be able to:

1. locate the current DuckDB production artifact in Google Drive
2. download it into the runner workspace
3. run ingestion, migration, validation, and compaction steps
4. upload a timestamped snapshot
5. promote the refreshed file back to the `prod` location
6. fail safely without corrupting or partially replacing the canonical production file

## Recommended operational pattern

### Pattern summary

Use GitHub Actions as the orchestrator, but treat Google Drive as an external artifact store.

High-level sequence:

1. **Authenticate to Google Drive** using a dedicated automation identity
2. **Download** `whenwin.duckdb` from `when-win/data/duckdb/prod/`
3. **Copy into runner-local workspace**
4. **Run update steps**
   - ingestion scripts
   - schema migrations
   - analytical materialization or refresh logic
   - validation queries
5. **Optionally compact/checkpoint** the DB before upload
6. **Upload a timestamped snapshot** to a snapshots folder
7. **Replace or promote** the production file only after validation succeeds
8. **Record metadata** such as refresh timestamp, git SHA, and schema/migration version

### Why this pattern is recommended

This avoids editing the Drive-hosted artifact in place and provides a clear promotion boundary between a working copy and the production copy.

## Authentication recommendation

For GitHub Actions, prefer one of these in order:

1. **Dedicated service account** with access to the Shared Drive and target folders
2. **Dedicated automation user** in Google Workspace with tightly scoped Drive access
3. **Personal OAuth credentials only as a temporary bootstrap**, not as the long-term pattern

Recommended GitHub secrets / variables:

- `GDRIVE_SHARED_DRIVE_ID`
- `GDRIVE_DUCKDB_PROD_FILE_ID`
- `GDRIVE_DUCKDB_PROD_FOLDER_ID`
- `GDRIVE_DUCKDB_SNAPSHOTS_FOLDER_ID`
- credentials secret appropriate to the chosen auth method

Use file IDs or folder IDs rather than depending only on name-based lookups.

## Workflow behavior recommendations

### Download step

- resolve the production file by Drive file ID where possible
- download to a runner temp location such as `${RUNNER_TEMP}/whenwin.duckdb`
- do not mutate files directly inside a sync-mounted Google Drive directory

### Update step

Within the action, the DB update should be performed against the local runner copy. Typical steps:

- install Python / DuckDB runtime
- run ingestion scripts from the repo
- apply SQL migrations from the repo
- run sanity checks, row-count checks, and key analytical validations

### Validation gates

Before promoting the updated DB, validate at minimum:

- database opens successfully
- expected schemas and tables exist
- migration version is current
- critical derived outputs can be regenerated or queried
- spot row counts or freshness indicators are within expected ranges

If validation fails, do not replace the production file.

### Snapshot upload

On successful validation:

- upload a snapshot to `snapshots/whenwin_YYYY-MM-DD_HHMMUTC.duckdb`
- capture metadata for traceability, ideally including:
  - UTC refresh timestamp
  - source repo commit SHA
  - migration version
  - workflow run URL or run ID

### Production promotion

After snapshot upload succeeds, update the production artifact.

Two acceptable patterns:

- **replace-in-place:** overwrite the existing `prod/whenwin.duckdb`
- **upload-then-promote:** upload a new file and update a metadata pointer or documented canonical reference

For the current setup, replace-in-place is acceptable if the workflow ensures the old file is not destroyed before the new upload is confirmed.

## Concurrency and locking guidance

Only one automation workflow should be allowed to update the production DB at a time.

Recommended controls:

- use a GitHub Actions concurrency group for DB refresh workflows
- restrict manual production refreshes to maintainers
- avoid simultaneous human and automation writes to `prod/whenwin.duckdb`

If there is any chance of concurrent updates, establish an explicit promotion policy before enabling scheduled jobs.

## Minimal workflow shape

The repository does not yet need a fully implemented action in this PR, but the recommended shape is:

1. checkout repository
2. authenticate to Google Drive
3. download current prod DB by file ID
4. run ingestion / migration / validation scripts
5. upload snapshot
6. promote updated DB to prod
7. emit refresh metadata in job summary

## Operational assumptions

This policy assumes:

- the Shared Drive is the canonical store for the database binary
- the repo remains the canonical store for code, schema, migrations, and documentation
- the production DB is updated by a controlled process, not ad hoc local edits
- Google Workspace access is already configured for the relevant maintainers

## Follow-up recommendations

1. Add a repository `.gitignore` entry for at least:

```gitignore
*.duckdb
*.duckdb.wal
*.duckdb.tmp
```

2. Add a `snapshots/` folder in the Shared Drive structure if it does not already exist.
3. Add a small metadata manifest, either in Drive or generated by the workflow, containing:
   - current prod file ID
   - last refresh timestamp
   - git SHA
   - migration version
4. When the workflow is implemented, use GitHub Actions `concurrency` to prevent overlapping production refresh jobs.
5. If the DB grows materially beyond the current size, revisit whether Drive remains the right artifact distribution mechanism or whether exports/parquet/object storage should supplement it.

## Ownership note

Jake owns Google Drive management and integration patterns for this project. Any future changes to storage location, automation credentials, retention policy, or promotion flow should be documented here and communicated to the rest of the team before they take effect.
