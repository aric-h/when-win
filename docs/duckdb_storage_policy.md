# DuckDB Storage Policy and Google Drive Sync Pattern

## Purpose

This document defines the storage, access, and automation pattern for the shared DuckDB database used by the `when-win` project.

The goals are:

- keep the DuckDB binary out of git permanently;
- give the team reliable shared access to the current production database;
- support local developer workflows and GitHub Actions workflows;
- reduce the risk of accidental overwrite or concurrent-write corruption; and
- keep Google Drive authentication and credentials out of the repository.

---

## Canonical production database location

The production DuckDB file currently lives in the project Shared Drive at:

```text
when-win/data/duckdb/prod/whenwin.duckdb
```

This is the canonical production database artifact for the project.

### Why this location is correct

This file should live in a **Shared Drive project folder**, not in the root of an individual user's My Drive.

Using the Shared Drive gives the team:

- team-owned access instead of person-owned access;
- cleaner offboarding/onboarding;
- clearer permissions for readers vs writers; and
- a stable location for future automation.

---

## Policy: the DuckDB file must never be committed to the repository

The repository stores:

- ingestion and migration scripts;
- SQL schema and DDL;
- the Streamlit app;
- documentation; and
- utility scripts that download and upload the database.

The repository must **not** store:

- `*.duckdb`
- `*.duckdb-wal`
- `*.duckdb-shm`
- local snapshots of the database
- Google credentials or service account JSON

### Required repository convention

The repo should maintain `.gitignore` coverage for DuckDB artifacts, for example:

```gitignore
*.duckdb
*.duckdb-wal
*.duckdb-shm
```

If local workflows standardize on a project path such as `data/whenwin.duckdb`, that path should also be ignored explicitly.

---

## Access policy

### Team access

Human team access should be granted through Shared Drive membership.

Recommended access split:

- **Writers / maintainers**: only the small group that runs ingestion, migrations, or production updates
- **Readers**: broader team members who need to inspect or download the database
- **Automation identity**: a dedicated credential for GitHub Actions or other non-interactive jobs

### Principle of least privilege

Not every collaborator needs write access to the production DuckDB file.

Recommended default:

- most team members: read access only;
- a smaller maintainer group: write access;
- GitHub Actions: only the minimum read/write access required for the specific automation.

---

## Operating model

### Treat the Drive file as a managed artifact, not as a live shared database

DuckDB should not be edited concurrently by multiple users against a cloud-synced location.

**Do not** treat Google Drive like a shared multi-writer database server.

Instead, the correct pattern is:

1. Download the canonical file from Google Drive to a local filesystem path.
2. Run ingestion, migrations, checks, or the app against the local file.
3. If the database was modified, upload the updated file back to the canonical Drive location.
4. Optionally create a timestamped snapshot before replacing the canonical production file.

### Why

This reduces risk from:

- concurrent writes;
- local sync conflicts;
- partial uploads;
- accidental corruption of the only shared copy.

---

## Recommended Google Drive structure

The current prod location is already aligned with the recommended structure.

Recommended Shared Drive layout:

```text
when-win/
  data/
    duckdb/
      prod/
        whenwin.duckdb
      snapshots/
        whenwin_2026-06-01T1719Z.duckdb
      staging/
        whenwin-staging.duckdb
```

### Directory purpose

- `prod/`: the canonical database used by the team and production-oriented workflows
- `snapshots/`: timestamped rollback copies before important updates
- `staging/`: optional database for testing migrations or ingestion changes before replacing prod

---

## Naming and versioning policy

### Canonical file

Use a stable canonical filename for production:

```text
whenwin.duckdb
```

### Snapshots

Use UTC timestamped snapshot names:

```text
whenwin_YYYY-MM-DDTHHMMZ.duckdb
```

Example:

```text
whenwin_2026-06-01T1719Z.duckdb
```

### When to create a snapshot

Create a snapshot before:

- major ingestion runs;
- schema migrations;
- destructive cleanup/backfills;
- bulk corrections;
- any workflow that could materially damage or regress the database.

---

## Local developer workflow

A local developer should work with a local copy of the database.

### Recommended flow

```text
download -> modify locally -> validate -> upload
```

### Recommended local path

Pick a standard local path and keep it ignored by git, for example:

```text
data/whenwin.duckdb
```

### Expected utility script pattern

The project should provide simple utility scripts such as:

```text
scripts/db/download_duckdb_from_drive.py
scripts/db/upload_duckdb_to_drive.py
```

Recommended behavior:

- `download_duckdb_from_drive.py`
  - authenticates to Google Drive using environment-provided credentials;
  - downloads the prod database to a local path;
  - optionally refuses to overwrite an existing local file unless explicitly requested.

- `upload_duckdb_to_drive.py`
  - validates that the local DB exists;
  - optionally creates a timestamped snapshot in `snapshots/`;
  - uploads the updated database to the canonical prod file location;
  - logs the file ID, timestamp, and actor when possible.

### Example CLI shape

The exact implementation can evolve, but the scripts should be easy for humans and CI to call. For example:

```bash
python scripts/db/download_duckdb_from_drive.py \
  --file-id "$WHENWIN_DUCKDB_PROD_FILE_ID" \
  --output data/whenwin.duckdb

python scripts/db/upload_duckdb_to_drive.py \
  --file-id "$WHENWIN_DUCKDB_PROD_FILE_ID" \
  --input data/whenwin.duckdb \
  --snapshot-folder-id "$WHENWIN_DUCKDB_SNAPSHOTS_FOLDER_ID"
```

---

## GitHub Actions pattern

GitHub Actions should follow the same artifact lifecycle:

1. authenticate to Google Drive;
2. download the prod DuckDB file to the runner;
3. run ingestion / migration / validation steps locally;
4. upload the updated file back to Google Drive if the workflow is intended to write changes.

### Important authentication note

Human access through a Google Workspace account does **not** automatically give GitHub Actions non-interactive access.

GitHub Actions needs its own credential path.

### Recommended automation credential model

Preferred:

- create a dedicated Google service account for automation, if allowed by Workspace policy;
- grant that identity access only to the relevant Shared Drive folder(s) or file(s);
- store the credential JSON in GitHub Actions **Secrets**;
- pass non-sensitive IDs via GitHub **Variables** or workflow env.

If service accounts are not permitted by Workspace policy, use the least risky supported non-interactive alternative approved by the team. Do **not** rely on manual personal login for CI.

### Suggested GitHub Actions secrets and variables

**Secrets**

- `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`

**Variables or env**

- `WHENWIN_DUCKDB_PROD_FILE_ID`
- `WHENWIN_DUCKDB_SNAPSHOTS_FOLDER_ID`
- `WHENWIN_DUCKDB_LOCAL_PATH`

### Why store IDs outside code

This keeps automation configuration changeable without editing scripts for every environment or file move.

---

## Recommended workflow behavior for write jobs

Any CI job that updates the database should:

1. download the canonical prod file;
2. run updates against the local DB;
3. run basic validation checks;
4. optionally create a snapshot;
5. upload the updated DB to the canonical prod file;
6. fail loudly if upload does not complete.

### Validation examples

At minimum, a write workflow should verify:

- the DB file exists after download;
- DuckDB can open it successfully;
- key sanity-check queries pass;
- the resulting file is non-empty and plausibly sized.

---

## Concurrency and change-management guidance

Only one writer should update the production DuckDB file at a time.

### Minimum team rule

Before a human performs a write update:

- announce intent to update prod;
- download the latest prod file immediately before editing;
- upload promptly after validation;
- notify the team that the update completed.

### Better long-term option

As automation matures, add one of:

- a lightweight lock file or lock metadata record;
- a staging DB promotion workflow;
- a write-through GitHub Actions workflow so updates happen through a single controlled path.

---

## What should and should not be hardcoded

### Safe to configure outside code

Keep these in environment variables, GitHub variables, or local shell config:

- Google Drive file IDs;
- snapshot folder IDs;
- local output path;
- environment name such as `prod` or `staging`.

### Do not commit

Never commit:

- service account JSON;
- OAuth client secrets;
- access tokens;
- refresh tokens;
- copied Drive share URLs intended only for operational use.

---

## Current project standard

For the `when-win` project, the current standard is:

- canonical prod database path in Shared Drive:

```text
when-win/data/duckdb/prod/whenwin.duckdb
```

- the DuckDB file is a managed external artifact, not a git-tracked asset;
- team members work from downloaded local copies;
- GitHub Actions must use secret-based non-interactive auth;
- future utility scripts should standardize download and upload behavior for both local developers and CI.

---

## Follow-up recommendations

1. Add or confirm `.gitignore` entries for DuckDB files and related local artifacts.
2. Implement the planned `scripts/db/download_duckdb_from_drive.py` utility.
3. Implement the planned `scripts/db/upload_duckdb_to_drive.py` utility.
4. Add a GitHub Actions workflow that exercises the documented download/update/upload path.
5. Introduce a staging DB and snapshot retention policy once write frequency increases.

---

## Notes

This document intentionally describes the **policy and recommended integration pattern**. It does not assume that the download/upload utility scripts already exist in the repository.
