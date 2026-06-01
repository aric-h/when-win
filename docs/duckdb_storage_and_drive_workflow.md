# DuckDB storage policy and Google Drive automation pattern

## Purpose

This document defines how the `when-win` team stores, shares, and updates the canonical DuckDB database outside GitHub.

It reflects the current project convention that:

- the DuckDB database **must not be committed to GitHub**
- Google Drive is the team's shared distribution and backup layer for the database file
- ingestion and schema logic remain in the repo, while the database artifact lives in Google Drive

## Canonical production database location

The current production DuckDB file lives in the shared Drive hierarchy at:

```text
when-win/data/duckdb/prod/whenwin.duckdb
```

Current Google Drive file link:

- `https://drive.google.com/file/d/184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi/view?usp=drive_link`

Current Google Drive file ID:

- `184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`

## Storage policy

### 1. GitHub is for code and documentation; Google Drive is for the database artifact

The repository should contain:

- ingestion scripts
- SQL schema / architecture files
- Streamlit app code
- helper utilities and documentation

The repository should **not** contain:

- `*.duckdb`
- `*.duckdb-wal`
- large derived database artifacts
- growing prod data files

### 2. The shared Drive file is the canonical team copy

`when-win/data/duckdb/prod/whenwin.duckdb` is the canonical production artifact for team access.

This file is intended to be:

- a shared source of truth for the current database state
- downloadable by the team for local analysis and app use
- replaceable by designated maintainers after validated updates

### 3. Team members should work from local copies, not directly against Drive

DuckDB is a single-file database. Google Drive is appropriate for file storage and distribution, but it is **not** a multi-user database host.

Recommended usage:

- download or sync a local copy before running analytical work or the Streamlit app
- make updates locally
- validate locally
- upload a controlled replacement only after checks pass

Avoid treating the Drive-hosted file as a live concurrently edited database.

### 4. Limit write access

Write access to the prod file and its containing folder should be limited to maintainers responsible for ingestion and release.

Recommended access pattern:

- small editor group for maintainers
- broader viewer access for consumers
- no public link sharing

### 5. Preserve a stable prod path

Consumers should be able to rely on a stable canonical location:

```text
when-win/data/duckdb/prod/whenwin.duckdb
```

If automation depends on the current file ID, prefer updating the existing Drive file in place so the file ID remains stable. If the file is replaced with a newly uploaded object, document the new file ID and update any automation or docs that reference it.

## Recommended Drive structure

The current structure is good. The team should keep the prod database in the existing path and add sibling folders as needed:

```text
when-win/
  data/
    duckdb/
      prod/
        whenwin.duckdb
      snapshots/
      staging/
```

Recommended use:

- `prod/`: canonical team database for daily use
- `snapshots/`: dated backups taken before or after major refreshes
- `staging/`: optional temporary location for validation or handoff before promoting to prod

### Snapshot naming

Recommended snapshot naming pattern:

```text
whenwin_YYYY-MM-DD.duckdb
whenwin_YYYY-MM-DD_HHMM.duckdb
whenwin_YYYY-MM-DD_post_ingest.duckdb
```

Examples:

```text
whenwin_2026-06-01.duckdb
whenwin_2026-06-01_2215.duckdb
whenwin_2026-06-01_post_ingest.duckdb
```

## Recommended Google Drive + GitHub Actions pattern

This section documents the recommended automation pattern for downloading, updating, validating, and re-uploading the DuckDB file.

### Important authentication note

The team's existing Google Workspace access through an individual user account does **not** automatically give GitHub Actions access to Google Drive.

GitHub Actions runs as its own non-interactive automation identity. To access the shared Drive from a workflow, the team should use one of these patterns:

1. **Preferred:** a Google service account with explicit access to the Shared Drive or target folders
2. **Fallback:** OAuth credentials with a refresh token for a dedicated automation-owned account

For long-term maintainability, prefer a service account or a dedicated robot account rather than a personal user token.

### Recommended automation flow

A GitHub Actions workflow should follow this sequence:

1. authenticate to Google Drive
2. download the current prod DuckDB file to the runner or workspace
3. run ingestion/update scripts against the local file
4. run validation / sanity checks
5. archive a timestamped snapshot in Drive
6. promote the validated file back to `prod/whenwin.duckdb`
7. log the update in workflow output and optionally in a metadata file or release note

### Why this pattern is recommended

This pattern keeps:

- GitHub as the source of truth for code
- Drive as the source of truth for the binary database artifact
- DuckDB updates serialized and auditable
- rollback straightforward via snapshots

## Suggested workflow design

### Option A: Update the prod file in place

Best when downstream references use a stable Drive file ID.

Pattern:

1. download `prod/whenwin.duckdb`
2. modify locally
3. validate
4. copy the current prod file to `snapshots/whenwin_<timestamp>.duckdb`
5. upload the validated local database back into the existing prod file object

Pros:

- stable file ID
- stable Drive link
- simpler for consumers using the current link

Cons:

- requires care to snapshot before overwriting

### Option B: Upload a new file and promote by convention

Best when the team prefers immutable uploads and path-based discovery.

Pattern:

1. upload a new timestamped file into `snapshots/` or `staging/`
2. validate / approve
3. either rename/move it into `prod/whenwin.duckdb` or update documentation/config to the new file ID

Pros:

- clearer artifact history
- more explicit promotion flow

Cons:

- may change file ID
- requires consumers and automation to tolerate promotion logic

### Recommended choice for `when-win`

For the current project state, **Option A is recommended** so the existing prod link remains stable, provided the workflow always creates a timestamped snapshot before replacing the prod content.

## GitHub Actions implementation guidance

### Suggested secrets / variables

Store Google integration settings in GitHub Actions secrets or organization secrets.

Suggested secrets:

- `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`
- `WHENWIN_DRIVE_FILE_ID`
- `WHENWIN_DRIVE_PROD_FOLDER_ID`
- `WHENWIN_DRIVE_SNAPSHOTS_FOLDER_ID`

Suggested values:

- `WHENWIN_DRIVE_FILE_ID=184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`

If the team chooses not to use a stable file ID, the workflow can instead locate the prod file by Drive folder + filename, but the stable ID approach is usually simpler.

### Suggested job stages

A workflow can be organized into these stages:

#### 1. Checkout and Python setup

- checkout repo
- install Python dependencies needed by ingestion scripts
- prepare a workspace path for the local DuckDB file

#### 2. Download current DuckDB file from Google Drive

- authenticate with Drive API
- download `whenwin.duckdb` locally
- optionally record file metadata and modified time in logs

#### 3. Run update scripts locally

Examples:

- run ingestion scripts against the downloaded local DB path
- run any migration or import steps required by the update
- keep all writes local until validation succeeds

#### 4. Validate before promotion

Recommended minimum checks:

- existing sanity checks pass
- DB file opens cleanly after update
- expected row counts or target league/season changes are present
- no obvious schema regressions

If validation fails, stop the workflow and do not upload the changed file.

#### 5. Snapshot current prod state

Before promoting the update:

- create a timestamped snapshot in `snapshots/`
- include UTC timestamp in the filename

This provides a simple rollback path.

#### 6. Promote validated DB to prod

- upload or replace the prod file at `prod/whenwin.duckdb`
- preserve the stable prod filename
- ideally preserve the stable file ID if the workflow updates the existing object in place

#### 7. Emit audit information

Capture at least:

- workflow run URL
- commit SHA
- timestamp
- maintainer or triggering actor
- snapshot filename / ID
- prod file ID after upload

## Operational guardrails

### Serialize updates

Only one workflow or maintainer should publish to prod at a time.

Recommended controls:

- GitHub Actions concurrency group for DB publish jobs
- restricted branch/workflow permissions for publish actions
- optional manual approval for prod-promoting workflows

### Keep the database out of pull requests

PRs should review:

- code
- workflow logic
- documentation

PRs should not attempt to store the binary DuckDB artifact in Git.

### Validate locally before large refreshes

For major schema-adjacent or historical data refreshes, validate in a local/staging copy before promoting to prod.

### Plan for growth

An 83 MB DuckDB file is manageable in Drive today, but the team should periodically review:

- file size growth
- update frequency
- snapshot retention policy
- whether Drive remains the right artifact store as the project matures

## Recommended follow-up tasks

1. Add a `.gitignore` if one is not already present, including at minimum:

   ```gitignore
   *.duckdb
   *.duckdb-wal
   ```

2. Create `snapshots/` under `when-win/data/duckdb/` if it does not already exist.
3. Decide on the automation identity:
   - service account preferred
   - dedicated automation user acceptable
4. Add a small helper script or workflow action for:
   - Drive download
   - local validation
   - snapshot upload
   - prod promotion
5. Document the local environment variable convention for consumers, for example:

   ```bash
   WHENWIN_DB=/path/to/whenwin.duckdb
   ```

## Summary

The `when-win` project should continue using Google Drive as the shared storage location for the canonical DuckDB file at:

```text
when-win/data/duckdb/prod/whenwin.duckdb
```

The recommended operating model is:

- keep DuckDB out of GitHub
- use Drive as the shared binary artifact store
- work on local copies
- restrict write access
- snapshot before every prod update
- use GitHub Actions only with explicit non-personal Google auth
- prefer preserving the stable prod file ID if automation or docs reference the current link
