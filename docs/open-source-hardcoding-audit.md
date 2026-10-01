# Open-source configuration and frontend/backend contract audit

Audit base: `7b03267` (`origin/main`), 2026-10-01. This is a static source audit, not proof of a deployed frontend/backend integration. Run the pre-launch checks against each deployment.

## Configuration sources

| Area | Current source observation | Status |
|---|---|---|
| Runtime settings | `common/config.py` loads root `config.json`, honors `CONFIG_PATH`, and reads environment overrides | VERIFIED in source |
| Direct config access | `common/audit.py` independently opens root `config.json`; this path does not use the shared `CONFIG_PATH` resolver | PARTIAL; consider consolidating |
| Secrets | `.env.example` documents environment settings; real secrets must not be committed | Requires deployment configuration |
| Runtime version | `config.json` → `system.version` is canonical and is checked against the latest dated changelog heading | Guarded |
| Service URLs | Shell scripts and service modules contain local/container defaults; deployment-specific values must be checked before use | Defaults, not universal endpoints |
| Model paths | `config.json` contains local model paths; model weights are not included in the repository | Requires operator-provided assets |

## Browser/API contract observed in source

The React app requests `GET /api/auth/metadata`, reads configured role options where available, and calls `/api/query`, `/api/chat`, `/api/dialog_history`, and `/api/stats`. Corresponding routes are present in the FastAPI `api/` package. `frontend/src/App.jsx` also has fallback metadata and development identity headers; those fallbacks are not evidence that every deployment enables the same auth mode.

The canonical application entrypoint is `app.py`. The presence of microservice directories does not prove those services implement the same contract or have been verified end to end with this frontend.

## Remaining hardcoding and integration risks

- `common/audit.py` resolves root `config.json` directly and may ignore `CONFIG_PATH`.
- `scripts/fault-injection.sh`, `scripts/start.sh`, and `scripts/verify-deployment.sh` use local URL defaults; the verification script accepts `BASE_URL`, while other scripts should be checked before use outside local development.
- React retains fallback title, role and development-auth metadata when the API does not provide values.
- Model names and local paths in `config.json` require external model assets.
- Auth headers such as `X-User-ID` are permitted for development flows; production must use the configured JWT path and disable development mode.

## Verification references

- Backend metadata, auth and route tests live under `tests/`; test presence alone does not establish deployed integration.
- Frontend build: `cd frontend && npm ci && npm run build`.
- Source audit commands used: `rg` for direct config reads, local URLs, identity defaults, frontend metadata and route strings across `app.py`, `common/`, `api/`, `frontend/src/` and `scripts/`.
- Full capability evidence and limitations: [repository truth audit](repository-truth-audit.md).
