# Independent engine verification — 17 October 2026

Baseline: main commit 627d72feb0b3a14cf13ce323f3a854084b3956a8.
Reliability correction: 234f0ed1ae82b4ecfe506b0298a05c879ec07d15 (PR 1).

## Executed evidence

`python -m pytest -q`: 11 passed, 25 dependency/lifespan deprecation warnings, Python 3.12.

A separate subprocess verification started Uvicorn against a temporary SQLite file in staging mode, created a synthetic client and campaign, imported one permitted synthetic lead, validated, approved and dispatched the campaign, terminated Uvicorn, started a new process with the same database, fetched the report and repeated dispatch.

Observed: mock_jobs before=1, after=1; mock_delivered after=1; retry new_mock_deliveries=0; live_messages_sent=0. The temporary test database was removed. This proves local process restart recovery, not cloud durability.

## Source correction

Source exists under matrix_marketing_execution_core/app/main.py, not root app/main.py. Previous missing-source assumptions were incorrect.

Approval persists audience membership. Dispatch and audience preview use that snapshot, while current suppression and permission revocations remain effective. Duplicate imports can restrict but not increase permissions or remove suppression. Legacy campaigns without an approval snapshot are blocked. Reports label execution as simulated and provider delivery as zero.

## Render inspection

Confirmed workspace: matrix360's workspace.
Existing service: srv-db960gavcj2c73djgn5g.
Source: Docker Hub image prathachauhan/matrix360-pratha:staging-v03; not linked to this GitHub branch.
Deploy dep-db960gqvcj2c73djgot0: update_failed.
Runtime logs: staging startup refused because MATRIX_EXEC_KEY did not meet the minimum 24-character requirement.
Service metadata: free plan, no HTTP health-check path configured. Persistent cloud database storage is not verified.
No hosted campaign acceptance test passed. No service settings or credentials were changed during this inspection.

## Outstanding release blockers

- Individual-user authentication, roles and cross-client authorization.
- Three approved qualification profiles integrated into campaign selection.
- Managed cloud persistence, migrations, backup restoration and durable background jobs.
- Authenticated provider adapters and verified callback evidence.
- Secure staging configuration and hosted end-to-end acceptance.
- Approval for actual outreach and controlled pilot.

Repository visibility remains public. No real client contacts or credentials were used in the patch/tests. No messages were sent. Other workstreams were not integrated.
