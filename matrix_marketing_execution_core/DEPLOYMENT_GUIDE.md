# v0.3 Deployment Guide — Controlled Staging Only

## Current safety boundary
This is mock-only software and contains no provider adapters. Its shared administrator header, SQLite data model, lack of tenant RBAC and campaign audience snapshots make it **unsuitable for real client production or public internet access**. `/console` serves a page that accepts the shared staging key; do not publish this console to untrusted users. Render blueprint is staging reference configuration, not authorization to deploy.

## Local Windows (no Docker)
1. Open PowerShell in the `matrix_marketing_execution_core` folder.
2. `py -m venv .venv` then `.\.venv\Scripts\Activate.ps1`.
3. `python -m pip install -r requirements.txt`.
4. `$env:MATRIX_ENV='development'`.
5. `$env:MATRIX_EXEC_KEY='a-local-only-development-key'`.
6. `$env:MATRIX_EXEC_DB='./marketing_execution.db'`.
7. `python -m uvicorn app.main:app --host 127.0.0.1 --port 8002`.
8. Open `http://127.0.0.1:8002/console`.
9. `python -m pytest -q` in a second terminal for automated tests.

## Docker preview (local-only)
1. Install Docker Desktop and start it.
2. In PowerShell, set `$env:MATRIX_EXEC_KEY='replace-with-at-least-24-random-characters'` (use a unique key).
3. Run `docker compose up --build`.
4. Open `http://127.0.0.1:8002/console` or `/ready`.
5. Data is stored in the named volume `pratha_demo_data`; do not delete that volume before backup.
6. `docker compose down` stops the container without deleting the volume.

## Hosted staging evaluation
The supplied `render.yaml` is a reviewable *staging reference* using a persistent disk, not a production recipe. Access must be protected by external identity-aware gateway/IP allowlist before any deployment. Enter the secret in the provider secret store, not in source files. Review HTTPS, domain, cost, backups, regional storage, storage durability, RBAC, CSRF and rate limits. There is no verified hosted deployment in this release. A staging URL is not considered delivered until a second-device browser test passes.

## SQLite-to-PostgreSQL migration gate
1. Stop writes; back up SQLite using its backup API.
2. Inventory tables and row counts (clients, campaigns, leads, jobs, events, audit).
3. Introduce versioned SQL migrations and PostgreSQL repository layer in a separate release.
4. Migrate FK parents first, then children, preserving UUIDs and timestamps.
5. Reconcile row counts, foreign keys and sample reports; run regression tests.
6. Rehearse restore and rollback in staging; do not remove SQLite files automatically.

## Independence
Audience and content JSON contracts are examples for future *authorized* integration only. They do not invoke, import or require Mayur or Tanmayi's services.
