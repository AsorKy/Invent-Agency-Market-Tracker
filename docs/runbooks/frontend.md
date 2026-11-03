# Frontend runbook

The frontend is the Claude Design export in `frontend/`, made functional against the gateway:
every list, KPI, chart and table comes from `/api/v1/*` or shows an honest empty state. It still
runs on the generated dc runtime (`frontend/support.js`, do not edit); the Next.js port of
REQ-FE-001 is a later decision.

## Open it

1. Start the stack: `make invent-tracker` (it also starts the `frontend` service), or, with the
   stack already up, `make frontend`.
2. Open http://localhost:3000 (`FRONTEND_PORT` in `.env` changes the port; it binds to
   127.0.0.1 only). `/` redirects to `/Invent%20Market%20Tracker.dc.html`.

## What it needs

- The `frontend` service: `nginx:1.27-alpine` serving `frontend/` read-only and the template
  `deploy/nginx/frontend.conf.template`. nginx proxies `/api/` to `gateway:8000`, so the browser
  stays on one origin: no CORS, and the session and CSRF cookies are first-party cookies.
- `frontend/unused/` holds design boards and uploads the app does not use. It is not served, not
  committed, and skipped by `ops/check_language.py`.
- Only the app files are served (allowlist): `*.dc.html`, `api.js`, `support.js`. Tests, docs
  and anything else under `frontend/` answer 404.
- The proxy plays the edge: when `EDGE_SECRET` is set it adds `X-Edge-Secret` server-side
  (empty means no header) and always replaces a browser-sent value. It drops browser copies of
  `X-Tenant-Id`, `X-User-*` and `X-Service-Token`. No token or secret is ever served to the page.
- Internet access in the browser: the dc runtime loads React 18.3.1 and ReactDOM from unpkg
  (with SRI) and Google Fonts. Babel is loaded only for `.jsx` imports, which the app does not use.
- Vendoring option (not done): download the three unpkg files into `frontend/vendor/`, define
  `window.__resources = {"<unpkg url>": "./vendor/<file>", ...}` in a small script before
  `support.js` (the runtime's `cdnScriptFor` honours it), add `vendor/` to the nginx allowlist
  and drop `https://unpkg.com` from the CSP. The minified files are not ASCII, so
  `ops/check_language.py` would need to skip `frontend/vendor/` as well.

## Session modes

On load the app calls `GET /api/v1/me`.

| Gateway setting | `/me` | Probe `GET /leads?limit=1` | Mode |
| --- | --- | --- | --- |
| any | 200 with a user | not called | Session: role, name and expiry from `/me` |
| `IDENTITY_VALIDATION_ENABLED=false` | 401 | 200 | Modo local (sin sesion): no session, role admin, every tab visible |
| `IDENTITY_VALIDATION_ENABLED=true` | 401 | 401 | Login screen |
| gateway unreachable | network error | not called | Full-page "No hay conexion con la API" with retry |

- Login posts `{email, password}` to `POST /api/v1/auth/session` (CSRF exempt). Identity sets an
  HttpOnly session cookie and a readable CSRF cookie (`session`/`csrf` in the local profile,
  `__Host-session`/`__Host-csrf` in production). `frontend/api.js` copies the CSRF cookie into
  `X-CSRF-Token` on every write; nothing is kept in localStorage except the theme.
- Two minutes before `expires_at` the app offers "Seguir conectado" (`GET /me` renews the
  cookie). A 401 during a session goes back to the login screen ("Tu sesion se cerro").
- "Salir" calls `POST /api/v1/logout`. In local mode the same button reads "Iniciar sesion" and
  opens the login screen, which offers to go back to local mode.
- Dev users: `services/identity/iam/management/commands/create_dev_users.py` creates
  `<role>@dev.local` with the passwords `DEV_USER_PASSWORD_<ROLE>` from `.env`, in tenant slug
  `dev`. Nothing in Compose or the Makefile runs it, so login works only after someone runs it
  inside the identity container.
- In local mode the gateway forwards `DEV_DEFAULT_TENANT_ID` only to routes without a role rule.
  Role-restricted routes (`/admin/tenant-config`, `/compliance/opt-out`, `/funnel/*`,
  `/validation/*`) answer `tenant_required`; the views show that message instead of data.

## Views and routes

| View | Routes |
| --- | --- |
| Mercado | `/companies/insights`, `/pulse/market`, `/pulse/pipeline-backlog`, `/pulse/source-efficiency`, `/pulse/time-in-pipeline`, `/pulse/unprocessed-signals` |
| Leads | `GET /leads` (state, service_line, segment, min_score, city, cursor), `POST /leads` |
| Lead detail | `GET /leads/{id}`, `GET /leads/{id}/brief`, `PATCH /leads/{id}/status`, `POST /leads/{id}/activities`, `POST /leads/{id}/outcomes`, `POST /projects` |
| Bandeja | `GET /leads?limit=200`, `PATCH /leads/{id}/status` (discovered to qualified) |
| Pipeline | `GET /pipeline/kanban`, `PATCH /leads/{id}/status` |
| Proyectos | `GET /projects`, `GET /funnel/projects/{id}`, `GET /validation/projects/{id}/{card}` |
| Territorio | `GET /territory/opportunity` |
| Ops | `GET /ops/health`, ingest counts from `GET /data/catalog` |
| Importaciones | `POST /imports/{entity}` (raw CSV), `GET /jobs/{id}` |
| Configuracion | `GET`/`PUT /admin/tenant-config` (ETag, If-Match), `POST /compliance/opt-out` |
| Validacion | `GET /validation/meta-ml`, `GET /projects` |
| Segmentos | `GET /segments` (answers 501 until the read model exists) |
| Datos | `GET /data/catalog`, `GET /data/{dataset}` |

Analitica, Modelos, Redes, Metricas and Investigacion have no public read route yet and say
"Sin datos todavia". The privacy page explains that opt-out is applied by an admin.

## Datos tab

Roles admin, strategist and analyst. The left list comes from `GET /api/v1/data/catalog`,
grouped by `origin`: "Bright Data (crudo)" (`bright_data_raw`: raw_snapshots, raw_records,
failed_records, failed_fetches, pending_snapshots) first, then "Derivadas" (entity_candidates, rues_queue,
companies, signals, leads, lead_scores, record_digests, fact_signals, daily_rollups), each with
its `count`. The table pages `GET /api/v1/data/{dataset}?limit=50&cursor=&q=`: headers from
`columns` (name and type), the text filter sends `q`, "Cargar mas" follows `next_cursor`, object
cells open a JSON viewer, `truncated_cells` shows the "truncado" badge, and "Exportar CSV" saves
the rows loaded so far.
A snapshot payload (25 KB to 800 KB) does not fit in a cell, so `raw_snapshots` shows only its
first 20,000 characters (badge "truncado"); `raw_records` expands every payload into one row per
Bright Data record, and its JSON viewer opens each record whole (a record above 20,000
characters is cut and badged the same way). A dataset with `unavailable: true` is listed but not fetched. The URL
hash (`#/data/<dataset>`) keeps the selection, so "Copiar enlace" works.

## Checks

- `make test-frontend`: node's built-in runner, no dependencies. `frontend/tests/api.test.js`
  covers `api.js` with a fake fetch (error kinds, CSRF, cache, pagination, session detection,
  CSV); `frontend/tests/components.test.js` compiles every dc script as the runtime does, mounts
  it against an empty and a populated fake API, checks that every `{{ name }}` resolves, and
  fails on mock data from the original export.
- `tests/unit/test_frontend.py` runs those under `make test` when node is installed and greps
  the app files for demo literals in any case. `tests/unit/test_compose.py` covers the service.

## Known limits

- The gateway forwards neither `Idempotency-Key` nor `X-Filename` upstream, so `POST /leads` and
  `POST /imports/{entity}`, which require the key, answer 422 through the gateway today. The UI
  sends the header and shows the API error.
- The gateway rate limit is 60 requests per minute per client IP; with `TRUST_PROXY_HEADERS=false`
  every browser shares the proxy's IP. Mercado makes six requests per load; GETs are cached for
  15 seconds. Raise `RATE_LIMIT_REQUESTS` locally if 429 banners appear.
- The lead detail does not return `rues_status` or decision makers; the app reuses them from the
  list row, so a lead opened by direct link shows "RUES sin dato" until opened from the list.
- Bandeja counts only the first 200 leads; there is no queue route for doubtful RUES matches
  (the Datos tab shows `rues_queue` and `entity_candidates` instead).
- Before boot the browser parses the raw template, so the console shows SVG "Expected length"
  errors and CSP `script-src-attr` reports for `{{ }}` attributes. They are inert: the CSP blocks
  those inline attributes and React attaches its own listeners.
- `frontend/Design System.dc.html` is a style-guide board with sample text, not an app view.
