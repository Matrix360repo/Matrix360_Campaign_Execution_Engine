# Independent campaign engine v0.4 — release candidate

This release is simulation-only. It is not authorization for production or actual outreach.

## User workflow

Sign in → select client → import CSV → record stated requirement → calculate explainable score → human qualification review → compose draft → validate → approve frozen audience/content → queue simulation → monitor/pause/resume → report and audit.

The console at `/console` follows these steps and includes help. Real accepted/delivered/engaged/converted counts remain zero. The legacy synchronous mock dispatch endpoint is retained only for development compatibility; hosted staging requires the durable queue. All execution is simulated.

## Security and data

- Individual accounts: admin, approver, operator, viewer; persisted eight-hour sessions; PBKDF2 password hashes; HttpOnly/SameSite cookies; Secure cookies outside development; origin checks for cookie-authenticated writes.
- Shared administrator key operates legacy development tests only. In hosted staging it can create the first user once, then cannot operate campaigns or create more users.
- Client membership is enforced on campaign, report, audit, audience, lead import, facts and qualification resources; list results are filtered.
- Approval is restricted to admin/approver. Viewers cannot mutate campaign data. Access changes revoke existing sessions. Password changes invalidate sessions.
- Failed login throttling and request/security audit records. No password, contact destination or provider exception text is written to audit records.
- Explicit channel-permission and suppression history records capture actor, evidence/reason and timestamp. Grants do not remove suppression; only an administrator can remove suppression. Evidence is recorded, not independently verified.
- Contact destination normalization and deduplication across sources within each client. Duplicate imports can restrict permissions and add suppression; cannot regrant permission or unsuppress.
- Scores use stated use, location, budget or industrial requirements. They never infer consent, caste, religion, ethnicity or wealth. Human qualification review is required. Profile scores are initial business rules requiring stakeholder acceptance before a real pilot.

## Reliability

Approval snapshots persist membership and content fingerprints. Durable queue state survives restart. Workers serialize claims and atomically write simulation jobs/events with uniqueness constraints. Eligibility and qualification are rechecked immediately before execution. Retry is bounded to three attempts with backoff; failures are not reported as delivery. Pause/resume are persisted.

SQLite is supported for local development. Hosted Render staging requires `DATABASE_URL` and refuses ephemeral local storage. PostgreSQL bridge and additive schema initialization are included; the CI matrix runs acceptance fixtures against isolated PostgreSQL 17 schemas and SQLite. Do not claim PostgreSQL or hosted acceptance until the corresponding CI/deployment evidence passes.

## Deployment

Canonical Blueprint: repository-root `render.yaml`. It creates one free staging web service, references the existing dedicated `matrix360-pratha-staging-db`, generates a bootstrap key inside Render, enables the embedded simulation worker and uses `/ready` for health checks. No other engine is required. The old Docker Hub service is not overwritten.

The database was provisioned on 17 October 2026 and expires on 16 November 2026. Free services sleep and free PostgreSQL has no managed backups. This is restricted synthetic staging, not a permanent commercial deployment. Continuous jobs and durable production retention require a separately approved always-on plan and backup policy.

The connector cannot apply Blueprints or expose the database connection secret. A workspace administrator must apply the reviewed Blueprint in Render; no credentials need to be pasted into chat. No production deployment is authorized by this release.

## Local operations

Set a development key and local DB path through the environment. Install `requirements.txt`, then run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8002`.

For the browser console, create the first administrator through the collapsed first-account form and sign in. For local automatic processing set `MATRIX_EMBEDDED_WORKER=1`; alternatively run `python -m app.worker` (`--once` for one batch).

Backup: `python -m app.backup backup /secure/path/backup-file`. Restore: set the target database to a new empty location, then `python -m app.backup restore /secure/path/backup-file`. Never overwrite the running database. PostgreSQL needs pg_dump/pg_restore on the operator host. Backups contain personal data and must remain outside Git with restricted access. PostgreSQL restore/recovery must be rehearsed before the real pilot.

## Remaining live-release gates

An authorized provider and approved sending identity/template; channel-specific consent evidence and unsubscribe management; signed and replay-protected delivery callbacks; explicit pilot audience/content approval; hosted acceptance; PostgreSQL restoration; business acceptance of qualification rules; always-on production resource decision. Production mode fails closed until these are resolved. No real messaging adapter is enabled or fabricated.

## Verified acceptance evidence

Local API and regression suite: 24 tests, including a real Uvicorn process restart with an individual session and pending job, scheduling, suppression after queueing, concurrent workers, bounded retries, cross-client access, qualification review and SQLite backup restoration. Final exact result is recorded with the release commit.

GitHub Actions on commit 8367300: SQLite and PostgreSQL acceptance passed. Browser acceptance passed on the preceding implementation commit aff1a8f (complete desktop/mobile flow; zero page errors and no mobile overflow). The latest final release commit must also pass CI before merge; these are historical checkpoints, not substitutes for the final checks.

The public Render JSON Schema accepted the quoted-off Blueprint configuration. The Render CLI is not installed, so no platform-aware CLI validation claim is made. Hosted service deployment, PostgreSQL restoration, real delivery and commercial readiness remain unverified until separately completed.
