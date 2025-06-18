# Security, deployment and backup notes

This repository is a production-planning reference application, not a managed
SaaS. Operators remain responsible for deployment hardening, secret rotation,
backups, OS patching and network access control.

---

## 1. Secrets

* Never commit `.env`, API keys, JWT keys, database credentials, PEM/private-key
  files, customer data or SQLite files.
* For a local install, `.env.example` contains deliberately weak development
  defaults. Copy it to `.env` and change the credentials before any shared
  environment.
* Production Compose uses a separate `.env.prod.example` template. Copy it to
  `.env.prod`; its required secret fields are blank so Compose fails closed
  until `SECRET_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `POSTGRES_PASSWORD` and
  `PUBLIC_HOST` are configured. It has no development fallback for those
  secrets. Generate a high-entropy secret, for example:

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(48))"
  ```

* Do not expose PostgreSQL. The production Compose network keeps the database
  and API private; the web nginx proxy is the public entry point. Terminate TLS
  at a trusted reverse proxy or load balancer. If a PostgreSQL password
  contains reserved URL characters, percent-encode it in the `DATABASE_URL`
  interpolation (or choose a URI-safe generated password).
* Provider API keys are encrypted with AES-256-GCM using a domain-separated
  key derived from `SECRET_KEY`; the API returns only a masked hint. A
  production deployment should prefer a KMS / managed secret store and protect
  `SECRET_KEY` independently. Rotating `SECRET_KEY` invalidates stored provider
  ciphertext; re-enter provider keys (or implement a controlled re-encryption
  migration) during planned key rotation. The backend logger redacts fields whose names
  contain `password`, `secret`, `api_key`, `apikey` or `token`.
* A live provider API key has not been configured in this demo. The mock AI
  provider is offline and makes no network request.

## 2. Identity and access

| Role | Access |
| --- | --- |
| Admin | User/AI settings, provider keys, resource changes, all plans and publishing. |
| Planner | Work orders/resources, solver runs, scenarios and explicit publishing. |
| Viewer | Read-only plans, Gantt, analytics and reports. |

Every route except liveness and login requires a valid access token. API input
is validated with Pydantic; resource edits, imports, schedule publication and
scenario changes are RBAC-checked and recorded in the audit log. The audit
endpoint is read-only.

Use HTTPS in production. Keep access tokens out of logs and URLs. Configure
CORS to only the browser origins you actually serve; the production nginx
setup keeps the API same-origin and does not publish the API port.

## 3. Uploads and import data

* Work-order imports accept CSV and JSON and reject files above the configured
  upload size (5 MiB by default).
* JSON input must be a list of objects or a `{ "work_orders": [...] }`
  envelope. Rows are validated individually and the `dry_run=true` mode never
  inserts any records.
* CSV parsing uses Python's standard `csv` module; uploaded file contents are
  not executed or written into a public directory.
* Do not import untrusted files into a database containing production data
  before checking the dry-run error report.

## 4. Database migrations and backup

* Docker startup applies the committed Alembic revision (`alembic upgrade
  head`) before the API serves traffic.
* SQLite development data is stored in the configured `DATABASE_URL` file. Use
  a consistent snapshot; with WAL mode, back up using SQLite's online backup
  API or stop the service and copy the database together with its WAL state.
* Production Compose uses PostgreSQL 16 and a named persistent volume. Add
  managed point-in-time recovery, encrypted off-site backups, access control,
  and a tested restore runbook before production use.
* Back up the API uploads volume if it contains retained operational data.
* Test migrations on a restored backup/staging copy before applying them to a
  production database. Do not run `reset` against real data: it deletes and
  reseeds the configured database.

## 5. Observability and availability

* `/api/health` is a liveness check; `/api/ready` verifies database readiness.
* Structured logs redact secret-shaped fields. Production sets `LOG_JSON=true`.
* Rate limits protect API endpoints; the CP-SAT concurrency guard returns HTTP
  429 when the in-process solver slots are full.
* Background job records and cancellation are in-process. Multi-replica
  deployments require a shared worker queue and distributed concurrency lock;
  do not scale the API horizontally without replacing these process-local
  controls.
* Apply dependency updates regularly. CI runs `pip-audit -r requirements-dev.txt`, `npm audit`,
  Bandit and a secret-file check.

## 6. Limitations and operational guidance

* This project is a single-site planning tool and does not connect to ERP/MES,
  CNC controllers, identity providers or cloud secrets managers.
* AI explanations are not authoritative instructions. Verify critical decisions
  with a human planner; the solver and validator remain the source of truth.
* The platform is not yet independently penetration-tested or certified for a
  regulated production environment. Review authentication/session policy,
  disaster recovery, database access, network rules and operational procedures
  for your plant before real-world use.
