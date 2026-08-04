# Dependency & config hygiene review

Source ticket: [#66](https://github.com/aric-h/when-win/issues/66) · Part of map [#62](https://github.com/aric-h/when-win/issues/62)

Scope: `scripts/requirements.txt`, `streamlit/requirements.txt`,
`.claude/settings.local.json`, `streamlit/.streamlit/config.toml`, plus a
full git-history scan for committed secrets and a `.gitignore` correctness
check. Findings verified against the actual dev `.venv` (Python 3.13.6)
and current CVE data via web search — not guessed from memory.

## Summary

| # | Area | Severity | Location |
|---|------|----------|----------|
| D1 | Three of four external deps have no upper bound and no lockfile | Medium | `scripts/requirements.txt:2-3`, `streamlit/requirements.txt:2-3` |
| D2 | `requests` floor predates a real, relevant CVE fix | Medium | `scripts/requirements.txt:2` |
| D3 | No pinned Python version anywhere in the repo | Low | n/a |
| I1 | Streamlit/DuckDB CVEs found in research — verified not applicable today | Info | n/a |

## Security

**No secrets found anywhere in git history.** Searched the *full* history
(`git log --all -p`, plus a filename scan across every commit), not just
the current tree, per the issue's explicit instruction:
- No file ever committed matching `.env`, `*secret*`, `*credential*`,
  `*.pem`, `*.key`, or similar sensitive-filename patterns.
- No diff content anywhere in history matches common key/token shapes
  (`api_key=`, `password=`, AWS access-key pattern, PEM private-key
  headers).
- All three league APIs used by the ingestion scripts are public and
  unauthenticated (confirmed again here — no auth headers, no API keys
  anywhere in `scripts/`), so there's nothing to leak in the first place.

**`.gitignore` correctness — verified, no issues.**
- `local_data/whenwin.duckdb` (the ~85MB DB) is excluded via
  `local_data/*` and confirmed untracked (`git ls-files local_data/`
  returns nothing).
- `.claude/*` is excluded, and — worth confirming explicitly since these
  files exist on disk — neither `.claude/settings.local.json` nor
  `.claude/launch.json` is tracked (`git check-ignore -v` confirms both
  match the rule; `git ls-files .claude` returns nothing). `settings.local.json`
  only contains a benign Bash permission rule, no secrets, but it's moot
  either way since it isn't committed.
- `.venv/` is excluded via `**/.venv`.
- No large or unexpected binaries anywhere in the tracked tree (checked
  `git ls-files` sorted by size — nothing over ~32KB, all source files).

**`streamlit/.streamlit/config.toml` — CORS/XSRF on secure defaults,
verified.** The file only contains `[theme.dark]` / `[theme.light]`
sections — no `[server]` block at all. Streamlit's documented defaults are
`server.enableCORS = true` and `server.enableXsrfProtection = true`; with
no override present, the app runs with both protections on. Nothing in
this repo weakens them.

## Dependency pinning & CVEs

**D1 — Unpinned floors, no lockfile (Medium).** Across both
`requirements.txt` files, only `duckdb` is pinned exactly
(`duckdb==1.4.4`, consistent between `scripts/requirements.txt:1` and
`streamlit/requirements.txt:1` — no drift). Everything else is a
floor-only range with no ceiling: `requests>=2.28.0`, `nba_api>=1.4.0`
(`scripts/requirements.txt:2-3`), `pandas>=2.0.0`, `streamlit>=1.46.0`
(`streamlit/requirements.txt:2-3`). There's no `requirements-lock.txt` /
`pip-compile` output / `poetry.lock` anywhere, so `pip install -r
requirements.txt` isn't reproducible: it resolves to whatever is newest
at install time. Checked the dev `.venv` directly — it currently has
`requests==2.34.2`, `nba_api==1.11.4`, `pandas==3.0.3` (a major version
past the `>=2.0.0` floor), `streamlit==1.58.0`. None of this is broken
today, but a fresh `pip install` next month could pull in a different
major version of any of these with no warning, and there's no way to
reproduce today's exact environment from the repo alone. Worth adding
upper bounds or a lockfile before this becomes public-facing.

**D2 — `requests` floor predates a relevant CVE fix (Medium).**
`requests` releases before 2.32.4 are affected by a `.netrc`
credential-leak issue on maliciously-crafted URLs (fixed 2025-06-09; the
project docs track it, no formal CVE ID assigned by requests itself but
tracked as CVE-2024-47081 by third parties — see sources). The current
floor, `requests>=2.28.0` (`scripts/requirements.txt:2`), doesn't encode
this fix; the dev `.venv` happens to have the patched 2.34.2 installed,
but that's incidental to *when* it was installed, not something the pin
guarantees. Exploitability here specifically is low — the ingestion
scripts (`scripts/ingest_nhl_api.py`, `ingest_mlb_api.py`) build every
request URL from a static base + our own date/season parameters, never
from external/attacker-supplied input — but there's no reason not to move
the floor up to `>=2.32.4` and close the gap explicitly.

**D3 — No pinned Python version (Low).** No `.python-version` or
`runtime.txt` anywhere in the repo; dev environment is Python 3.13.6
(`.venv/pyvenv.cfg`). Not causing problems today, but worth deciding
explicitly before the deployment map's containerization work
([#78](https://github.com/aric-h/when-win/issues/78)) — a `Dockerfile`
built without an explicit Python version pin will drift over time the
same way the unpinned package floors do.

**I1 — CVEs researched, confirmed not applicable to this repo today.**
Checked current advisories for all five direct dependencies:
- **Streamlit CVE-2025-1684** (client-side-only file-type validation in
  `st.file_uploader`, allowing upload of disguised malicious files; fixed
  in 1.43.2, current pin resolves to 1.58.0 which is already patched) —
  moot regardless of version: grepped the whole `streamlit/` tree and
  confirmed `st.file_uploader` isn't used anywhere in this app. Noting for
  awareness only, in case a future feature adds file upload.
- **DuckDB CVE-2025-59037** (npm package supply-chain compromise) — this
  affects the `@duckdb/*` **JavaScript/WASM** packages, not the Python
  `duckdb` PyPI package this repo uses. Not applicable.
- **DuckDB CVE-2025-64429** (encryption-at-rest implementation
  weaknesses, fixed in 1.4.2) — this repo doesn't use DuckDB's
  encrypted-database feature (no `PRAGMA add_key` / encrypted-open
  anywhere in `scripts/` or `streamlit/`), so not exploitable regardless;
  the pinned `1.4.4` is past the fix anyway.
- **DuckDB CVE-2026-58139** (`aws` extension credential-redaction
  bypass) — grepped for `LOAD aws`/`LOAD httpfs`/`s3://` across
  `scripts/`, `streamlit/`, `sql/`: none found. Not applicable today, but
  flagging forward-looking since `docs/` already has S3/MotherDuck
  migration notes tied to the deployment map — worth remembering if that
  extension gets adopted later.

## Sources

- [Vulnerability Disclosure — Requests 2.34.2 documentation](https://docs.python-requests.org/en/latest/community/vulnerabilities/)
- [Python Requests security vulnerabilities, CVEs (cvedetails.com)](https://www.cvedetails.com/product/29258/Python-Requests.html?vendor_id=10210)
- [Cato CTRL: New Streamlit Vulnerability Enables Cloud Account Takeover Attack](https://www.catonetworks.com/blog/cato-ctrl-new-streamlit-vulnerability/)
- [CVE-2025-1684 | Snyk](https://security.snyk.io/vuln/SNYK-PYTHON-STREAMLIT-8749606)
- [CVE-2025-59037: DuckDB NPM Package Compromise (SentinelOne)](https://www.sentinelone.com/vulnerability-database/cve-2025-59037/)
- [CVE 2025-64429 (osv.dev)](https://osv.dev/vulnerability/CVE-2025-64429)
- [CVE-2026-58139 (THREATINT)](https://cve.threatint.com/CVE/CVE-2026-58139)
- [Streamlit config.toml docs — server.enableCORS / enableXSRFProtection defaults](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
