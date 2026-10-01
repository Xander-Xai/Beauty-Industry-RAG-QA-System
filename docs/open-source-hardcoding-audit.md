# Open-Source Hardcoding Audit and Frontend/Backend Contract

This project is domain-configurable. The default configuration still models a cosmetics-industry RAG assistant, but deployers should be able to rebrand and reshape roles without editing source code.

## Current Remediation

| Area | Previous hardcoding | Current adjustment |
|------|---------------------|--------------------|
| Frontend title/subtitle | React literals such as `RAG 智能问答` | Loaded from `GET /api/auth/metadata`, backed by `config.json.ui` |
| Frontend role list | Fixed admin/R&D/quality/regulatory/sales options in `App.jsx` | Loaded from `config.json.ui.role_options` |
| Browser identity | Fixed `X-User-ID: web_user` | Uses JWT in production; only uses configured anonymous user and role masks when `auth.dev_mode=true` |
| Auth contract | Frontend did not know whether JWT or dev headers were expected | `GET /api/auth/metadata` exposes `auth.dev_mode`, `auth_required`, `jwt_enabled`, `login_enabled`, and configured role metadata |
| Runtime env loading | Docs required `.env`, but plain `python3 app.py` did not load it | `common.config.py` now auto-loads project-root `.env` before parsing config |
| Frontend coverage | `/api/query` / `/api/dialog_history` / `/api/stats` existed only on backend | React page now exposes `Single Query`, `Session`, and `Stats` entry points |
| Config path | Some backend routes read `config.json` directly | Main app and RAG routes now use `common.config.get_config_dict()` and honor `CONFIG_PATH` |
| Service config path | Several service/helper modules opened project-root `config.json` directly | Gateway generation, admission, cache, rerank service files, offline runner, Airflow DAG, auth helpers, and alerting now use shared config helpers or safe defaults |
| Browser tab title | Static `index.html` title | React sets `document.title` from backend metadata at runtime |
| Media route query | Raw `doc_id` interpolated into Qdrant scroll filter | Reuses `common.auth.validate_doc_id()` before querying |
| Alert footer | Email alerts used a fixed product name | Alert emails now use `system.name` |
| API auth (stats/metrics) | `/api/stats` and `/api/metrics` had no auth, exposing system metrics | v2.5.0: added `Depends(require_identity)`, monitoring tools must now configure Bearer token |
| CORS production safety | Missing `CORS_ORIGINS` in production only logged a warning | v2.5.0: production mode hard-stops startup if `CORS_ORIGINS` is unset |
| ES security | Elasticsearch ran without authentication | v2.5.0: `xpack.security.enabled=true` in docker-compose; `bm25_retriever.py` reads `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` from env vars |
| Login rate limiting | In-memory only (not shared between workers) | v2.5.0: added Redis-backed rate limiting (`ratelimit:login:{ip}`) with automatic degradation to in-memory |
| Session state per-worker | `SessionState` lived only in process memory, lost on multi-worker deployments | v2.5.0: `SessionState` persists to Redis (key `session:{id}`, TTL=7200s) with silent degradation to in-memory |
| Auth route lifecycle | `@app.on_event("startup")` / `@app.on_event("shutdown")` deprecation warnings | v2.5.0: migrated to `lifespan` context manager, eliminating 17 FastAPI deprecation warnings |

## Runtime Contract

The browser client should discover runtime metadata before sending chat requests:

```http
GET /api/auth/metadata
```

Response shape:

```json
{
  "app": {
    "title": "化妆品行业知识问答助手",
    "subtitle": "面向化妆品行业知识库的检索增强问答助手",
    "version": "2.0.0"
  },
  "auth": {
    "dev_mode": false,
    "auth_required": true,
    "jwt_enabled": true,
    "login_enabled": true,
    "anonymous_user_id": "web-user"
  },
  "rbac": {
    "default_role": "public",
    "roles": { "admin": 2147483647 },
    "departments": { "all": 0 },
    "role_options": [
      { "key": "public", "label": "Public Visitor", "role_mask": 0, "dept_mask": 0 }
    ]
  }
}
```

Production calls to `/api/query`, `/api/chat`, `/api/media/{doc_id}`, `/api/stats`, and `/api/metrics` must use:

```http
Authorization: Bearer <access_token>
```

Development-only calls may use `X-User-ID`, `X-Role-Mask`, and `X-Dept-Mask` when `config.json.auth.dev_mode=true`.

Current frontend alignment additions:

1. The browser now uses `auth_required` and `login_enabled` to decide whether sign-in is mandatory and whether the backend is actually ready to issue JWTs.
2. `POST /api/query`, `GET /api/dialog_history`, and `GET /api/stats` now have first-class UI entry points instead of being backend-only capabilities.
3. The default development role has been lowered to `public` instead of `admin`.
4. **Stats and metrics endpoints now require JWT** (v2.5.0) — Prometheus scraping requires a valid Bearer token.

## Configuration Fields for Open Source Deployments

Update these fields before publishing a demo or deploying a fork:

| Config path | Purpose |
|-------------|---------|
| `system.name` | API and logs system name |
| `ui.app_title` | Browser title and app header |
| `ui.subtitle` | Browser app subtitle |
| `ui.anonymous_user_id` | Development-only anonymous identity |
| `ui.default_role` | Default development role key |
| `ui.role_options` | Browser-visible role/dept choices |
| `rbac.roles` | Backend role bitmask definitions |
| `rbac.departments` | Backend department bitmask definitions |
| `domain_keywords` | Domain routing keywords |
| `prompts.system_prompt` | Domain prompt override |
| `permission_rules.rules` | Offline document permission mapping |

## Remaining Brainstorming Backlog

These are real-world hardcoding risks that should be addressed next, but were not all changed in this pass:

1. Move service URL defaults such as `http://rewrite-service:8101` into a single service-discovery config helper shared by the API gateway routers.
2. Convert shell scripts that call `http://localhost:8000` into `BASE_URL=${BASE_URL:-http://localhost:8000}` style parameters.
3. Add a `config.example.json` for open-source users and document when to copy it to `config.json`.
4. Add a setup command for creating the first admin user instead of relying on manual database operations.
5. Review model names and local paths so all deployment-specific values are either documented defaults or environment overrides.
6. Make Grafana/Prometheus dashboard titles configurable or replace them with neutral defaults.

## Verification Checklist

- Backend metadata endpoint: `pytest tests/test_auth_metadata.py -q`
- Frontend contract build: `cd frontend && npm run build`
- Static hardcoding scan: search source for fixed user IDs, role labels, product names, and direct `open("config.json")`.
- RAGAS dataset validation: `python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl`
- Full test suite: `pytest tests/ -q` (target: 640 passed)
