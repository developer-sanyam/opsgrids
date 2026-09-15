# Production readiness

**Assessment: the five original blockers are closed. Not yet deployable — see What is left.** This page exists to say exactly why, so the decision to
deploy is made with the list in front of you rather than around it.

Nothing here is a reason not to keep building. Several items are cheap. But shipping this to a
paying customer today would expose real data on a system whose tenant isolation has no
enforcement underneath it.

---

## Blockers — do not deploy with these open

### ~~1. Tenant isolation has no enforcement layer~~ — **RESOLVED**
Row-level security is live on the `account` schema: 11 policied tables, `FORCE` set, and the
application connecting as a NOBYPASSRLS role whose transactions are bound to one organization by
the chokepoint. A session bound to one tenant cannot read another's rows even by primary key with
no filter at all, and cannot write into another tenant — asserted in
`test_cross_tenant_isolation.py`, which is the acceptance test the rest of ADR-0002 existed to
make pass.

Isolation is now a property of the database rather than of every query remembering its filter.

### ~~2. The application connects to Postgres as a superuser~~ — **RESOLVED**
Each service connects as its own NOBYPASSRLS role, granted DML on its own schema only. Migrations
run as an owner role that serves no traffic, and a third read-only role serves the cross-tenant
directory lookups. account-service can no longer read `auth.credentials`, TRUNCATE is withheld,
and audit tables are INSERT/SELECT only.

### 3. ~~The internal service token is a shared static secret~~ — **RESOLVED** (with a caveat)
Both internal channels now carry short-TTL signed tokens: `/internal/v1` calls mint per-callee
tokens, and the gateway's identity-forwarding hop mints a per-request token bound to the
forwarded user and organization. The raw signing key never travels, and production refuses the
`dev-only-`/`ci-only-` placeholders. The caveat: the signing key is still one shared HMAC
secret (per-service keypairs or mTLS is the end state), and no network policy yet makes
`/internal/v1` unreachable from ingress — see `docs/api-improvements.md` §1.

### 4. The cross-tenant suite covers the storage layer, not every route
`test_cross_tenant_isolation.py` proves the *database* refuses: reads, primary-key lookups and
writes across tenants all fail. What does not exist yet is the generated per-route sweep —
enumerate every route, seed in org A, call with an org-B token, assert never `200`. Hand-written
cross-org tests are always incomplete; a generated one stays complete as routes are added.

Materially smaller than it was: a route that forgets its filter now returns nothing rather than
somebody else's rows.

### ~~5. No end-to-end test~~ — **RESOLVED**
`make e2e` starts account, auth and the product gateway as real processes and drives the whole
journey over HTTP: sign in through the gateway (which makes auth call account's internal API for
real), then read the organization with the issued token (gateway verifies, resolves permissions
from account, forwards identity; account binds the tenant and reads under row-level security).

It found a configuration bug on its first run — see [discoveries.md](discoveries.md).

## What is left

The blockers are gone; these are not blockers but they are not nothing:

- **No deployment of any kind.** No Kubernetes manifests, no CI/CD beyond test-running CI, no
  environment to deploy to.
- **No products.** The platform authorizes access to services that do not exist yet.
- **Observability is thin** — structured logs and an outbox probe, no metrics or tracing.
- **Backups are unconfigured**, which becomes mandatory before the PulseBoard migration.
- **The service signing key is shared** between services rather than per-service keypairs.

---

## Serious gaps

| Gap | Consequence |
|---|---|
| **Readiness does not check Redis** | A service reports ready, then fails every permission-changing write and refuses all gateway traffic |
| **Outbox-lag monitoring is a probe, not an alert** | `GET /health/outbox` counts every tenant's unpublished rows through `opsgrids_outbox`, so a stalled relay is visible — but nothing scrapes it yet, and a probe nobody alerts on is a stall nobody notices |
| **No DLQ alerting** | Consumers ship in product, subscription, payment and console; failures retry once then park in `{queue}.dlq`. `tools/dlq.py` inspects and replays, but nothing alerts on depth yet |
| **No metrics, tracing or error tracking** | Correlation ids are propagated and nothing collects them |
| **Cross-tenant reads are not audited** | Closed: employee PII unmasks write `entity.sensitive_read` rows, and the account directory reads log `directory.cross_tenant_read` |
| **No backup or restore** | Mandatory before the PulseBoard migration, and it needs to be *rehearsed* |
| **No rate limiting beyond per-caller counters** | No global or per-IP ceiling on the unauthenticated surface |
| **Secrets come from `.env`** | No secret manager, no rotation story |

---

## What is genuinely solid

Worth stating, because the list above is not the whole picture:

- **Failure directions are right.** Rate limiting, permission-version checks and permission
  resolution all fail **closed**. Losing Redis refuses traffic rather than admitting it
  unchecked. A permission bump that cannot be recorded fails the write rather than reporting a
  revocation that did not happen.
- **The token design holds up.** Per-audience tokens, no permission list, a `pv` claim checked
  per request, one private key in one service, verification through JWKS everywhere else.
- **Header spoofing is handled.** Inbound identity headers are stripped before the gateway writes
  its own — the defect that would otherwise let a caller simply claim to be someone else.
- **The operator surface is genuinely separated.** A different audience, a different gateway, and
  staff-only routes that answer `404` rather than `403` so a prober learns nothing.
- **Writes are transactional end to end.** State change, audit row and outbox event commit
  together or not at all.
- **Money is represented correctly** before any billing code exists.
- **Tests run against real PostgreSQL.**

---

## The shortest path to "could deploy for a pilot"

Roughly in order. This is not the full backlog — it is the subset that changes the answer:

1. Move off the superuser: create the roles properly, grant least privilege, split the migration
   role from the application role. **No RLS in this step** — it is a prerequisite, and doing it
   first means the RLS change can actually be tested.
2. Wire the tenant chokepoint so the GUC is set on every transaction.
3. Land RLS policies, `account` schema first.
4. Write the generated cross-tenant isolation suite. This is what turns "we believe it is
   isolated" into evidence.
5. Replace the internal static token with signed service tokens — done, on both the
   `/internal/v1` and gateway-forwarding channels; making `/internal/v1` unreachable from
   ingress (network policy) is still open.
6. Add the outbox-lag probe and Redis readiness check. Done; the probe and the relay run as `opsgrids_outbox`, which is what makes the count cross-tenant under row-level security.
7. Write the end-to-end journey suite.

Items 1–4 are one coherent piece of work and should be sequenced together. Items 5 and 6 are
small and independent — they can go at any point.
