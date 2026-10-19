# Claude Job Agent — Rebuild MVP

This is an operational **review-first prototype**, not a full auto-apply system. It retrieves postings from employer-provided Greenhouse and Lever public APIs, scores jobs using transparent rules, stores them in SQLite, and can optionally draft truthful resumes using Claude. It NEVER submits applications.

## EC2 installation

Extract into `/home/ubuntu/claude-job-agent`. Ensure `data` and `resumes` are writable by UID 1000 (the Ubuntu user is usually UID 1000): `id -u`.

```bash
cd ~/claude-job-agent
sudo docker compose build
sudo docker compose run --rm agent python -m app.main health
sudo docker compose run --rm agent python -m app.main scan
sudo docker compose run --rm agent python -m app.main list
```

Change `config/settings.json` to add Greenhouse board slugs or Lever company slugs. Boards may reject requests or return no results; verify URLs and terms. Remote flags are heuristic and require manual checking. Scans do not cost Claude API money.

To draft a resume, replace `config/verified_profile.txt` with your *verified* facts. Configure `ANTHROPIC_API_KEY` securely in the host environment, not in chat or repository. Then run `sudo --preserve-env=ANTHROPIC_API_KEY docker compose run --rm agent python -m app.main tailor 'JOB_ID'`. Check current Claude model name in Anthropic's documentation and set `CLAUDE_MODEL` as needed. API use is separately billed.

## Limitations

No salary floor enforcement where pay is missing; no broad internet crawler; no automatic applications; no browser automation; no daily scheduler enabled; no UI. Data is saved to `data/jobs.sqlite3`, resume drafts to `resumes/`. The initial score is keyword-based, not Claude-based. Avoid enabling unattended operation until sources, costs, legal permissions, and results are validated. Do not expose Docker API or SQLite publicly.

## Phase 1: explicit SQLite migrations

Database schema changes are explicit: application `health`, `scan`, `list`, and
`tailor` commands now require a migrated database and never create or migrate one.
The dashboard and worker also do not run migrations. `JOBS_DB` overrides the
application database path; the existing `/workspace/data/jobs.sqlite3` default is
unchanged. The migration CLI requires `--database` and has no default path.

Run a safe local trial using a temporary database (Python standard library only):

```bash
trial_dir=$(mktemp -d)
python3 -m app.migrations --database "$trial_dir/jobs.sqlite3"
python3 -m app.migrations --database "$trial_dir/jobs.sqlite3"  # already current
JOBS_DB="$trial_dir/jobs.sqlite3" python3 -m app.main health
python3 -m unittest discover -s tests -v
```

Tests create their own temporary databases; they do not read configuration,
production data, secrets, or resumes, call external APIs, or start services.

Version 1 creates the original `jobs` schema or adopts a compatible existing
schema. Version 2 adds only missing `classification`, `classification_reason`, and
`classified_at` text columns. Existing rows, extra columns, classifications,
review statuses, approvals, and timestamps remain untouched. Added columns are
nullable with no backfill: migration does not classify jobs or decide eligibility.

`schema_migrations` records each version, name, and UTC application timestamp.
All pending versions and history writes run in one `BEGIN IMMEDIATE` transaction;
failure rolls back the entire run. Concurrent migration runners serialize with
a 30-second lock timeout. Rerunning validates the schema and history without
reapplying versions. Unknown/noncontiguous history and missing required columns
fail closed. Do not manually edit migration history or change released versions;
append new versions for future changes. No downgrade or destructive repair is
provided.

Before any future production rollout, obtain explicit authorization, take a
verified SQLite-consistent backup (including any WAL state), and test a separate
copy with this exact code. Coordinate a maintenance window with writers before
migration. The command for an authorized target is
`python3 -m app.migrations --database /explicit/path/to/database.sqlite3`.
This implementation has **not** been run against production. Do not use Docker
commands from the installation section as part of the temporary trial: current
Compose services mount the production data directory. Running services are
unchanged; deploying the new code requires a separate, authorized rollout. The dashboard image now builds from the repository root and
includes `app.migrations` for schema validation. Rebuild the dashboard image for
a future rollout. Dashboard connections require the current migration history
and schema before querying or updating jobs; they never run migrations. An
unmigrated or incompatible database produces the existing generic HTTP 500 response.
