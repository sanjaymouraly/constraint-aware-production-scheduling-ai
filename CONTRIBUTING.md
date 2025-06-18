# Contributing to Intelligent Production Scheduling AI

Thanks for taking the time to contribute. This document explains how to set the
project up on **Windows (PowerShell)** and **Linux / macOS**, how the test and
quality gates work, and the rules that keep the scheduling results trustworthy.

---

## 1. Core principle: constraints decide, AI only explains

This is the rule every change must respect:

* The **constraint solvers** (FCFS baseline and the CP-SAT optimiser) and the
  **independent validator** decide what is schedulable.
* The **AI subsystem** may only *describe* an already validated schedule.  It
  must never invent operations, machine assignments, start/end times or metric
  values, and it must never relax a hard constraint.
* Every number quoted by the assistant is checked against an `EvidencePack`
  built from the validated schedule.  Unsupported numbers are flagged
  (`unsupported`) rather than silently accepted.

A pull request that lets the model influence the schedule is **rejected**.

---

## 2. Ground rules

| Topic | Rule |
| --- | --- |
| Tests | Never delete, skip, xfail or weaken an assertion to make a build green. |
| Benchmarks | Never hardcode solver output; the numbers in the README come from real runs. |
| Optimality | Never label a time-limited solution `OPTIMAL`.  Use `FEASIBLE`. |
| Validation | Every schedule (baseline, optimised, scenario) must pass `app/scheduling/validator.py`. |
| Secrets | No API keys, tokens or `.env` files in the repository. |
| Publicity | Do not push to a remote or publish a package without explicit maintainer approval. |

---

## 3. Development setup

### 3.1 Windows (PowerShell)

```powershell
# After publishing, replace YOUR_GITHUB_OWNER with your GitHub account or organization.
git clone https://github.com/YOUR_GITHUB_OWNER/intelligent-production-scheduling.git
cd intelligent-production-scheduling

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements-dev.txt

cd backend
python -m app.cli reset --scale medium
cd ..

cd frontend
npm install
npm run dev
```

If `Activate.ps1` is blocked by the execution policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3.2 Linux / macOS

```bash
# After publishing, replace YOUR_GITHUB_OWNER with your GitHub account or organization.
git clone https://github.com/YOUR_GITHUB_OWNER/intelligent-production-scheduling.git
cd intelligent-production-scheduling

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements-dev.txt

cd backend && python -m app.cli reset --scale medium && cd ..

cd frontend && npm install && npm run dev
```

The one-command helpers in `scripts/` do exactly the same thing:

* `scripts/setup.ps1` / `scripts/setup.sh` – install everything and seed data.
* `scripts/start-backend.ps1` / `scripts/start-backend.sh` – run the API.
* `scripts/start-frontend.ps1` / `scripts/start-frontend.sh` – run the web app.
* `scripts/test.ps1` / `scripts/test.sh` – run the full quality gate.

### 3.3 Docker

```bash
docker compose up --build
docker compose exec api python -m app.cli reset --scale medium
```

The API is served on <http://localhost:8000>, the web app on
<http://localhost:5173>.

---

## 4. Quality gates

```bash
cd backend
ruff check app tests          # lint
ruff format --check app tests # formatting
mypy app                      # strict-ish typing
python -m pytest -q            # unit, property, API and integration tests

cd ../frontend
npm run lint
npm run typecheck
npm run test                  # Vitest component tests
npm run build                 # production bundle
npx playwright test           # end-to-end tests (needs the servers running)
```

The GitHub Actions workflows in `.github/workflows/` run these gates:

* `ci.yml` — backend lint, formatting, type checks and non-slow tests, an
  Alembic migration smoke test, frontend lint/type/unit/build checks,
  Playwright E2E, dependency/static security audits and a Docker Compose
  startup/migration/health smoke test.
* `benchmarks.yml` — the slow solver benchmarks and the `slow` test marker,
  on demand or weekly.

Both use the deterministic mock AI provider, so CI never needs a paid
credential.

### Measuring a benchmark

A wall-clock-limited solver is **not** reproducible: a faster host simply
finds more improving solutions in the same number of seconds. Use the
deterministic budget and a single worker so that the numbers are identical on
every machine:

```bash
cd backend
python -m app.cli benchmark --scale medium --time-limit 900 --deterministic-time 30 \
    --workers 1 --output ../docs/benchmarks/medium.json
```

Add `--reference 2024-05-13T00:00:00Z` to pin the instant the plan is made
for. Update `docs/benchmarks.md` with the **measured** values — never with
values copied from a previous run.

---

## 5. How the code is organised

```
backend/app/domain/       pure time & calendar algebra (no I/O, no ORM)
backend/app/scheduling/   instance builder, FCFS, CP-SAT, validator, metrics
backend/app/services/     orchestration: run/persist/publish, scenarios, auth
backend/app/ai/           provider adapters, grounding/verification, client
backend/app/api/routes/   HTTP surface (74 REST operations under /api)
backend/app/seed/         deterministic demo factory
frontend/src/components/  shared UI (incl. the SVG Gantt renderer)
frontend/src/pages/       the ten application screens
```

The scheduling package must stay importable without FastAPI, SQLAlchemy or
httpx so it can be unit tested in isolation.

---

## 6. Adding a constraint

1. Express it in `backend/app/scheduling/instance.py` (data) and enforce it in
   **both** `fcfs.py` and `cpsat.py`.
2. Add a matching check to `backend/app/scheduling/validator.py` using a new
   `H<number>` code.  The validator deliberately shares **no** code with the
   solvers so that a shared bug cannot hide.
3. Extend `backend/tests/test_validator.py` with an injected violation and
   `backend/tests/test_solvers.py` with a positive case.
4. Document the new code in `docs/architecture.md`.

---

## 7. Adding an AI provider

1. Implement the `Provider` protocol in `backend/app/ai/providers.py`.
2. Register the kind in `PROVIDER_KINDS` and add it to `PROVIDER_DEFAULTS`
   (base URL, default model, list of models for discovery).
3. Add an adapter test in `backend/tests/test_ai.py` covering: success,
   timeout, HTTP error, invalid key and malformed response.
4. Make sure the adapter is covered by the offline/mock path so the test suite
   never needs network access.

---

## 8. Commit and pull request style

* Small, focused commits with imperative messages (`fix: ...`, `feat: ...`).
* Reference the issue in the description.
* Describe **how you verified** the change (command + result).
* If a change affects the scheduling behaviour, include the new benchmark
  numbers in the description and update `docs/benchmarks.md`.

---

## 9. Reporting bugs

Please include the scale (`small`/`medium`/`large`), the engine, the solver
time limit, the validation report of the affected schedule and the relevant
`docker compose logs api` excerpt.  A schedule that fails validation is a
bug in the solver or the validator - both are in scope.

---

## 10. Licence

By contributing you agree that your contributions are licensed under the
[MIT Licence](LICENSE).
