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
