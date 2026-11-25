# Contributing

Thank you for helping improve this public engineering reference. Changes should be reproducible, scoped, and consistent with the evidence and data boundaries in this repository.

## Development setup

- Python 3.11 is the supported runtime and CI version.
- Node.js 20 is required only for frontend work (`frontend/package-lock.json` is the npm lockfile).
- Docker Compose v2 is optional for local integration work. Model weights, API credentials, approved corpora and production infrastructure are not included.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

For frontend changes:

```bash
cd frontend
npm ci
npm run build
```

## Branches and commits

- Branch from the latest `main`; use a short-lived branch named for the change.
- Use concise conventional commit subjects such as `docs: clarify evaluation setup` or `fix: reject stale evidence`.
- Keep each commit focused. Do not commit generated reports, model weights, local state, `.env`, keys or credentials.

## Checks

Run the relevant checks before opening a pull request:

```bash
ruff check .
ruff format --check .
python3 scripts/check_repo_consistency.py
python3 scripts/check_public_repository_boundary.py
python3 -m pytest tests/ -q
```

For frontend changes, also run `npm ci && npm run build` from `frontend/`. Integration and external-runtime commands are documented in the relevant guides; do not claim those checks ran unless their actual environment and evidence are available. GitHub branch protection and required Actions checks remain the merge gate.

## Documentation and evidence

- Update the README, canonical architecture, configuration contract, evidence map or validation record when a change affects their facts.
- Keep `docs/README.md` and generated contracts in sync with new or moved canonical documents.
- Distinguish implemented code, deterministic tests, local real-dependency validation and production validation. Do not turn a skipped or blocked run into a result.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes; do not invent a version or release.

## Data, models and security boundaries

- Add only synthetic data or materials with documented permission to redistribute; register data assets in `DATA_MANIFEST.json` and explain provenance in `DATA_PROVENANCE.md`.
- Do not add private employer/client information, real customer records, credentials, unlicensed regulatory text, model weights or generated runtime artifacts.
- Treat user history, retrieved passages and tool/model text as untrusted input. Preserve current RBAC, epoch, Evidence Gate and cache-isolation requirements.
- Report vulnerabilities through the private reporting route described in [SECURITY.md](SECURITY.md), not in a public issue.

## Pull requests

Use the PR template. Describe the purpose, scope, linked issue, exact commands and outcomes, security/data impact, documentation impact and known limitations. Do not self-label an implementation or CI pass as deployment or production acceptance.
