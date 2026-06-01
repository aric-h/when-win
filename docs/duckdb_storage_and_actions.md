# DuckDB storage policy and Google Drive / GitHub Actions operating pattern

## Purpose

This document defines how the when-win team should store, access, update, and automate the project DuckDB database outside the Git repository.

## Canonical production database

- **Shared Drive path:** `when-win/data/duckdb/prod/whenwin.duckdb`
- **Current Google Drive file link:** `https://drive.google.com/file/d/184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi/view?usp=drive_link`
- **Current Google Drive file ID:** `184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`

This file is the canonical production DuckDB artifact for the project.

## Storage policy

### 1) The DuckDB file does not belong in Git

The repository should continue to store:

- ingestion scripts
- SQL schemas and queries
- Streamlit application code
- operational notes and documentation

The repository should **not** store:

- `*.duckdb`
- large versioned database binaries
- ad hoc local database snapshots

### 2) Google Drive is the system of record for the canonical DB artifact

The canonical production database should live in the Shared Drive, not in a contributor's local machine and not in the Git repository.

Current canonical location:

- `when-win/data/duckdb/prod/whenwin.duckdb`

### 3) Use Google Drive as artifact storage, not as a live multi-user database host

DuckDB is a single-file database. Team members should **not** treat the Shared Drive copy as a live collaborative database file.

Recommended operating rule:

- download the file locally before opening it for analysis or app use
- perform writes locally or in controlled automation
- close the database cleanly before publishing an updated version
- publish back to the canonical Shared Drive location only through an approved update workflow

Avoid opening and editing the same Drive-hosted file concurrently from multiple synced desktops.

### 4) Single-writer publishing model

The team should operate the production DB with a **single-writer** model:

- one approved person or automation job updates the canonical file at a time
- everyone else consumes the published file as read-only unless they are explicitly running the update workflow
- write operations should be serialized via process and CI concurrency controls

### 5) Shared Drive permissions

Recommended permissions pattern:

- **Editors:** only maintainers responsible for ingestion / publish operations
- **Viewers:** all other project consumers who only need to download and inspect the DB

Because the file is in a Shared Drive, access should be granted via the drive or containing folder rather than by ad hoc one-off shares whenever possible.

### 6) Recommended sibling folders

The current production path is appropriate. To support safer operations, the following sibling folders are recommended under `when-win/data/duckdb/`:

```text
when-win/data/duckdb/
├── prod/
│   └── whenwin.duckdb
├── archive/
│   └── whenwin_YYYY-MM-DD_HHMMUTC.duckdb
├── staging/
│   └── whenwin_candidate.duckdb
└── manifests/
    ├── latest.json
    └── checksums.txt
```

Recommended usage:

- `prod/`: the stable published database used by the team
- `staging/`: temporary validation target for candidate builds
- `archive/`: immutable timestamped snapshots for rollback and audit
- `manifests/`: machine-readable metadata such as updated timestamp, schema version, row counts, source run, and checksum

### 7) Versioning and rollback

Use a stable production filename:

- `whenwin.duckdb`

Use timestamped archive filenames:

- `whenwin_2026-06-01_1719UTC.duckdb`

At each successful publish:

1. create an archive snapshot
2. validate the new database
3. update the canonical production file
4. update manifest metadata

Retain enough historical snapshots to support rollback.

## Recommended Google Drive + GitHub Actions pattern

This section describes the recommended automation pattern for scheduled or manual DB refreshes.

## Core design principle

**GitHub Actions should never commit the DuckDB file into the repository.**

The workflow should:

1. authenticate to Google Drive using non-interactive credentials
2. download the canonical DB from Drive to the runner
3. run ingestion / transformation / validation locally on the runner
4. publish the updated DB back to Drive
5. optionally create an archived snapshot and update metadata

## Authentication pattern for automation

A GitHub Actions runner cannot rely on a maintainer's interactive Google session. CI needs its own non-interactive Google auth.

Recommended options, in order:

### Preferred: service account dedicated to automation

Use a dedicated Google service account for CI and grant it access to the relevant Shared Drive folder or files.

Store required credentials in GitHub Actions secrets, for example:

- `GCP_SERVICE_ACCOUNT_JSON`
- `WHENWIN_DRIVE_FILE_ID=184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`
- `WHENWIN_DRIVE_PROD_FOLDER_ID`
- `WHENWIN_DRIVE_ARCHIVE_FOLDER_ID`
- `WHENWIN_DRIVE_MANIFEST_FOLDER_ID`

If the Shared Drive does not allow direct service-account access under current org policy, use a Workspace-approved delegated pattern instead.

### Alternate: delegated Workspace automation identity

If the org prefers not to share Drive content directly with a service account, set up an approved automation identity with delegated access. This requires Workspace admin support and is more operationally complex, but it is acceptable if Shared Drive policy requires it.

## GitHub Actions workflow pattern

### Trigger modes

Recommended triggers:

- `workflow_dispatch` for manual controlled publishes
- `schedule` for recurring refreshes after the workflow is stable
- optionally `push` to selected ingestion branches for non-prod validation only

### Concurrency control

Use workflow-level concurrency so only one DB publish job can run at a time.

Recommended pattern:

- concurrency group such as `whenwin-duckdb-prod`
- disable overlapping runs for the production publish workflow

This supports the single-writer model.

### Recommended high-level job sequence

#### 1) Authenticate to Google

The workflow authenticates using the CI service account or delegated identity.

#### 2) Download the current production DB from Google Drive

Download `whenwin.duckdb` to the runner workspace.

Important note: use the current file ID (`184qaQsc7Zf7uvdMEqgfjwk9XtlFtaOgi`) or a stable manifest-driven lookup rather than manually scraping a browser link.

#### 3) Run ingestion and database update steps locally on the runner

Examples:

- fetch new raw data
- run Python ingestion scripts
- apply SQL migrations if needed
- run integrity checks / sanity checks
- run schema validation and row-count smoke tests

All modifications should happen on the runner-local copy, not directly in Drive.

#### 4) Validate before publish

At minimum, validate:

- database opens successfully
- expected schemas / tables exist
- basic sanity checks pass
- file size is within expected range
- optional checksum is generated

#### 5) Archive the pre-publish or post-publish DB

Upload a timestamped copy to `archive/` before or alongside the production update so the team can roll back if needed.

#### 6) Update the canonical production file in place

Preferred behavior: **update the existing Drive file in place** so the production file keeps the same Drive file ID and shared link.

Why this matters:

- links in documentation remain valid
- any automation or local config that uses the stable production file ID does not need to change
- the team has a single durable canonical object

If the update mechanism cannot perform in-place replacement safely, then:

1. upload a new candidate file to `staging/`
2. validate it
3. promote it to `prod/`
4. update the manifest to point consumers at the new file ID

The in-place update pattern is simpler if supported by the chosen Drive API implementation.

#### 7) Update a manifest

Recommended manifest fields:

- production file ID
- production file name
- updated timestamp (UTC)
- archive snapshot name
- schema version
- source workflow run ID
- checksum
- notable migration notes

This can be stored in Drive under `manifests/latest.json`.

#### 8) Log and notify

At minimum, preserve:

- GitHub Actions run logs
- manifest metadata
- archive snapshot

If the team adds notifications later, publish a short summary to Slack or email.

## Operational recommendations

### Do not mount the Shared Drive production file as the app's everyday write target

For local development and Streamlit testing, download the DB and use a local path such as:

```bash
WHENWIN_DB=/path/to/whenwin.duckdb streamlit run streamlit_app/app.py
```

### Keep production and development copies separate

Recommended convention:

- Shared Drive `prod/whenwin.duckdb` = canonical production artifact
- local `data/whenwin.duckdb` = disposable developer working copy

### Protect the production publish workflow

Recommended controls:

- restrict who can run the prod publish workflow
- use a protected GitHub environment for production secrets
- require manual approval for prod updates until automation is well tested

### Do not use GitHub release assets or repository commits as the DB source of truth

The database is already beyond the size that should comfortably live in source control, and it will continue growing. Drive should remain the binary artifact store unless the team later adopts a more specialized data storage layer.

## Minimum implementation checklist

Before automating production updates, complete the following:

- [ ] confirm `*.duckdb` is ignored by Git
- [ ] create `archive/`, `staging/`, and `manifests/` folders in the Shared Drive
- [ ] decide whether CI will use a service account or delegated Workspace identity
- [ ] grant the CI identity least-privilege access to the Shared Drive location
- [ ] store Drive IDs and Google credentials in GitHub Actions secrets / variables
- [ ] implement workflow concurrency for prod publish jobs
- [ ] add validation and sanity checks before publish
- [ ] update a manifest after each successful publish
- [ ] document rollback steps using the archive snapshot

## Recommended ownership

- **Integration & Storage (Jake):** Shared Drive layout, CI auth strategy, upload / publish process, manifest convention
- **Database & Analytics:** schema validation, data checks, migration safety, publish readiness criteria
- **Data Ingestion:** ingestion commands and update sequencing
- **UI:** local DB consumption pattern and prod/dev path expectations

## Summary

The approved operating pattern for when-win is:

- keep DuckDB out of Git
- store the canonical production DB in the Shared Drive
- treat Drive as artifact storage, not as a live shared database host
- update the DB through a single-writer publish flow
- use GitHub Actions only as an orchestrator that downloads locally, updates locally, validates locally, and re-uploads to Drive
- preserve a stable production file/link where possible and maintain timestamped archives for rollback
