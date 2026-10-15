# Production readiness

**Assessment (2026-10-15): deployed and serving, with open risks the owner has accepted or has yet
to act on.** Production is `main` @ `1dbeebd4` on one Azure VM (ADR-0012, ADR-0013). The five
blockers this page once listed are closed. This page now says what is still open, so decisions about
customers, money and growth are made with the list in front of you.

How to operate it: [operations.md](operations.md) and
[`infrastructure/edge/RUNBOOK.md`](../infrastructure/edge/RUNBOOK.md).

---

## Closed since the first assessment

| Former blocker | State |
|---|---|
| Tenant isolation had no enforcement layer | Closed. Forced RLS on a transaction-local GUC, NOBYPASSRLS application roles (ADR-0002). `test_cross_tenant_isolation.py` exists in 17 services |
| The application connected as a superuser | Closed. Each service has its own role with DML on its own schema; migrations run as an owner role that serves no traffic |
| The internal service token was a static secret | Closed, with a caveat. Both internal channels carry short-TTL signed tokens. The signing key is still one shared HMAC secret |
| No end-to-end test | Closed. `make e2e` drives real services over HTTP (76 journeys collected) |
| No deployment of any kind | Closed. `make deploy` is preflight, build, `caddy validate`, migrate, recreate, health |

## Open risks

| Risk | State | What closing it needs |
|---|---|---|
| **No off-box backup** | `BACKUP_S3_*` is empty. The nightly script keeps the dump on the box's own disk and exits non-zero. Pre-deploy backups of 2026-10-14 are on-box only | An owner-provided S3-compatible bucket, then `make edge-backup` and `make edge-restore-drill` |
| **Memory** | Planned 3945 MB against MemTotal 3862 MB, under the owner-accepted ceiling `EDGE_BUDGET_ACCEPTED_MB` | An owner decision to resize the box or accept a higher ceiling. Insights (+99 MB) is blocked on it |
| **CI is not running** | GitHub Actions billing is blocked. Checks are run locally with `make gate` | The owner fixes billing |
| **Payments are in test mode** | Live provider keys are not on the box | Owner supplies keys and follows the RUNBOOK's payments section |
| **GST portal not connected** | Tax invoices print without IRN and QR | Owner connects the portal and supplies supplier GST facts |
| **No error tracking, metrics or tracing** | No SDK ships. Errors surface as log lines and 5xx in the access log | A dependency (memory the box lacks), a setting, a test |
| **No zero-downtime deploy, no staging** | A deploy restarts changed containers. `caddy validate` and `make gate` are the only checks before production | A second box (ADR-0012's stated trigger) |
| **No log shipping** | Logs are in Docker's json-file driver | A log sink |
| **Cross-tenant tests are per service** | 17 of 34 services have the suite. No generated per-route sweep exists | Enumerate routes, seed in org A, call with an org-B token, assert never `200` |
| **Shared HMAC key for internal tokens** | One secret for all services | Per-service keypairs or mTLS |
| **`/internal/v1` reachability** | No network policy makes it unreachable from ingress | See [api-improvements.md](api-improvements.md) section 1 |
| **Secrets come from `.env`** | No secret manager. Rotation is by hand (RUNBOOK, "Rotating the JWT signing key") | A secret manager |
| **Rate limits are per caller** | No global or per-IP ceiling on the unauthenticated surface | A Caddy or gateway limit |
| **`make secrets` is red on `main`** | Unbaselined hits in test files | Baseline or allowlist them |

## What is solid

- **Failure directions are right.** Rate limiting, permission-version checks and permission
  resolution fail closed. Losing Redis refuses traffic.
- **Tokens.** Per-audience, no permission list, a `pv` claim checked per request, one private key in
  one service, JWKS everywhere else.
- **Header spoofing is handled.** Inbound identity headers are stripped before the gateway writes its own.
- **The operator surface is separate.** Different audience, different gateway, staff-only routes answer `404`.
- **Writes are transactional.** State, audit row and outbox row commit together or not at all.
- **Money is integer minor units**, displayed by the currency's own exponent.
- **Tests run on real PostgreSQL**, with a deploy gate that refuses a missing `.env` key, a placeholder secret, a dirty checkout or an over-budget memory total.
- **Monitoring exists.** `ops-monitor` emails on outbox lag, DLQ depth, API readiness, disk, backup freshness and certificate expiry.

## Before you tell a customer "yes"

1. Is there an off-box backup that has been restored once? Today: no.
2. Is the memory ceiling still the one the owner accepted? `make edge-preflight` prints the memory row.
3. Is the product you are selling live? Check [features.md](features.md) and [SPEC.md](../SPEC.md), not a plan.
4. Are payments and GST claims limited to what the box can do? Test-mode payments and no portal connection mean neither is live.
