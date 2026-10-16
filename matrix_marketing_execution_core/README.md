# Matrix 360 Interactive Campaign Execution Core v0.3

**Standalone, mock-only deployment foundation. NOT production-ready.**

Previous v0.2 campaign APIs, console, database logic, test fixtures and simulation behavior are preserved. New in v0.3: Dockerfile, Docker Compose, staging blueprint, environment template, readiness/health endpoints, fail-closed production mode, versioned future audience/content contracts and deployment guide.

Start here: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md). Open `/console` for visual operation; `/docs` remains developer-only.

No runtime integration with Mayur or Tanmayi. No provider sending. No live data should be uploaded to an unprotected demo.

**Known blockers:** shared API key, SQLite storage, no true tenant RBAC, no campaign-specific audience snapshot, no verified consent evidence, no async worker, no production data migrations. `/ready` checks the SQLite store only.
