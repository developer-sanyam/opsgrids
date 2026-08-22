# Production readiness

**Assessment: not production ready.** This page exists to say exactly why, so the decision to
deploy is made with the list in front of you rather than around it.

Nothing here is a reason not to keep building. Several items are cheap. But shipping this to a
paying customer today would expose real data on a system whose tenant isolation has no
enforcement underneath it.

---

## Blockers — do not deploy with these open

### 1. Tenant isolation has no enforcement layer
**The single biggest one.** `organization_id` is on every row and every query filters on it, by
convention. Row-level security — the net that is supposed to catch a forgotten filter — is
designed and **not implemented**, and is inert for three independent reasons.
[ADR-0002](adr/0002-row-level-security.md) is accepted, so this is buildable rather than blocked.

One missing `WHERE organization_id = …` is a cross-tenant data leak with nothing to stop it, and
it returns `200` with a well-formed body. No error, no log line.

### 2. The application connects to Postgres as a superuser
`DATABASE_URL` uses a role that is `rolsuper` and `rolbypassrls`, and that role owns every table.
Beyond defeating RLS, it means any SQL injection or code defect has unrestricted database access,
including `DROP`.

The correct roles exist in the bootstrap and are unused, holding only `CONNECT`.

### 3. The internal service token is a shared static secret
A single header value, defaulting to `dev-only-internal-token`, guards an API that reads across
every tenant. One leak exposes every user, membership and organization name on the platform.

### 4. No cross-tenant isolation test suite
The acceptance gate for tenancy — enumerate every route, seed data in org A, call with an org-B
token, assert never `200` — does not exist. Hand-written cross-org tests are always incomplete; a
generated one stays complete as routes are added.

### 5. No end-to-end test
Nothing runs the services together. Two real outage-class defects reached the repository because
each side stubbed the other, and both suites were green. See [discoveries.md](discoveries.md).

---

## Serious gaps

| Gap | Consequence |
|---|---|
| **Readiness does not check Redis** | A service reports ready, then fails every permission-changing write and refuses all gateway traffic |
| **No outbox-lag monitoring** | A stalled relay is completely silent — the process looks healthy while the queue grows without bound |
| **No DLQ alerting** | Moot today (no consumers) — mandatory before the first one |
| **No metrics, tracing or error tracking** | Correlation ids are propagated and nothing collects them |
| **Cross-tenant reads are not audited** | `record_sensitive_read` exists and is called by nothing |
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
5. Replace the internal static token with signed service tokens, and make `/internal/v1`
   unreachable from ingress.
6. Add the outbox-lag probe and Redis readiness check.
7. Write the end-to-end journey suite.

Items 1–4 are one coherent piece of work and should be sequenced together. Items 5 and 6 are
small and independent — they can go at any point.
