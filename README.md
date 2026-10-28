# ◈ ATLAS — Operations Intelligence Command Center

A full-stack, responsive, local-first operations dashboard. Built with **FastAPI, SQLite, WebSockets and vanilla JavaScript**, without a frontend build step.

## Features

- Executive command dashboard with live KPIs and operational breakdowns
- Project portfolio with per-project task completion metrics
- Drag-and-drop Kanban: backlog → to do → in progress → review → done
- Incident management with severity, investigation status and resolution notes
- Infrastructure inventory with environment and health tracking
- Event-based automation rules, conditional execution and execution counters
- Append-only audit activity log; real-time WebSocket refresh across tabs
- Analytics studio for task priority, asset health, and incident risk
- Full CRUD JSON REST API, OpenAPI explorer, JSON backups and CSV ZIP export
- Responsive dark interface with search, modal editor and keyboard shortcut Ctrl+K
- SQLite WAL mode, SQL query parameterization, API field validation
- Optional API key guarding `/api/` and `/ws`; Docker Compose example binds to loopback by default

## Start on Fedora / Ubuntu

```bash
cd atlas-command-center
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8181
```

Visit **http://127.0.0.1:8181**. API docs: **http://127.0.0.1:8181/docs**.

## Docker

```bash
cp .env.example .env
# Edit .env and set a strong ATLAS_API_KEY for nonlocal access.
docker compose up -d --build
```

Compose binds to `127.0.0.1:8181` on the Docker host to avoid exposing an unauthenticated admin app to the internet. To access from another LAN device, use an SSH tunnel (for example `ssh -L 8181:127.0.0.1:8181 user@server`) or deliberately configure a protected reverse proxy. **Do not publish the app directly to the internet without strong authentication, TLS, and access control.** API key support is a basic single-operator guard, not multiuser authentication. If you enable it, set `localStorage.setItem('atlas_api_key','YOUR_KEY')` in your browser console, then reload. Keep your API key secret.

## Examples

```bash
curl http://127.0.0.1:8181/api/meta/overview
curl -X POST http://127.0.0.1:8181/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"data":{"title":"Verify offsite backups","priority":"critical","status":"todo"}}'
curl http://127.0.0.1:8181/api/meta/export?format=json -o backup.json
curl 'http://127.0.0.1:8181/api/meta/export?format=csv' -o backup.zip
```

Add `-H 'x-api-key: YOUR_KEY'` to API calls if authentication is enabled.

### Automations

Rules listen for `tasks.created`, `tasks.updated`, `incidents.created`, `incidents.updated`, `assets.created`, and `assets.updated`. Optional conditions compare an exact record field and value. Actions: `log` (record an execution), `set_priority` (tasks only), and `set_status` (tasks/incidents/assets with supported status values). Runs occur in the same database transaction as record changes, with recursion prevented. The included sample rule logs the creation of critical incidents.

### Database

`data/atlas.db` is created automatically and seeded with sample projects, tasks, assets, and an incident on first launch. `data/` is persistent with Docker Compose and excluded from Git. Back it up regularly. `/api/meta/export` exports records, but **does not implement restore/import**. Delete sample records in the UI if desired.

### Security & limitations

This is a self-hosted single-workspace prototype, not a hardened multi-tenant SaaS. No user accounts, role-based permissions, HTTPS terminator, email integration, or external monitoring agents are implemented. Health values are manually tracked, not system measurements. Browser needs internet only for optional Google Fonts; the app itself does not depend on an external API. The API key protects API endpoints but not static HTML; use a VPN, SSH tunnel, or trusted proxy for sensitive use.

## Tests

```bash
pip install pytest httpx
pytest -q
```

## License

MIT. See LICENSE.
