# Backlog

What is next, in order, and what is blocking. Delivered work is at the bottom.

> This list previously lived in the build harness's ledger under `.claude/`, which is local
> tooling and no longer tracked in the repository. It lives here now so it ships with the code.

Each item is a **slice**: a capability that crosses the whole stack — schema, API, UI, tests —
rather than a horizontal layer. Nothing is "done" until its tests pass.

---

## Blocked, and first

### Row-level security
**[ADR-0002](adr/0002-row-level-security.md) is accepted and its four open questions are
answered.** In progress, in two slices.

**Slice one — prerequisites, no policies: DONE** (`c708f60`, `<pending>`).

Services connect as per-service NOBYPASSRLS roles and migrations as an owner role; grants are
applied by the migration run so a new service cannot ship without them; TRUNCATE and
audit-rewrite are withheld by privilege; the test suite runs as the app role so the split is
visible to it. The tenant chokepoint opens each request's transaction and binds it to the
caller's organization as the first statement — routes no longer open transactions of their own.
auth's principal routes (switch-org, me, MFA enrolment, password change) are on the chokepoint
too, and outside production the binding is read back and asserted immediately after it is set.

Exit gate met: the GUC is set on every transaction, it does **not** survive the transaction,
and a static check refuses any route that takes a bare session or opens its own transaction.
Verified by deliberately introducing a bypass and watching the guard fail.

A correction to the record: an earlier commit message said migrations had been "silently doing
nothing". They had not — `alembic` exits 255 on failure and `make migrate` surfaces it. That
claim came from reading the exit code of `tail` through a pipe rather than of `alembic`.

**Slice two — policies: DONE.**

Row-level security is live on the `account` schema. Eleven tables carry a `tenant_isolation`
policy with both `USING` and `WITH CHECK`; `users`, `permissions` and `processed_events` are
exempt for the reasons ADR-0002 records. `roles.organization_id` is NOT NULL and the template
machinery is gone. The cross-tenant reads that sign-in depends on run on a third role —
BYPASSRLS but SELECT-only on seven identity tables — so they keep working without handing the
migration role to a module that only reads.

`test_cross_tenant_isolation.py` is the acceptance test: a bound session sees only its own rows,
cannot read another tenant's row by primary key, cannot write into another tenant, and an unbound
session sees nothing at all.

**Still open here:** the generated per-route cross-tenant sweep, and the index recomposition
ADR-0002 lists (`audit_logs` first — its five single-column indexes no longer lead with the
tenant column now that the policy qual is ANDed into every query). Neither blocks correctness;
the second is a performance cliff waiting for volume.

The relay and the stall probe now run on `atmora_outbox` (BYPASSRLS, SELECT and UPDATE on
`outbox` only), so neither is blind to the rows the app role can no longer see — which both were
the moment the policies landed, since each connected as the app role with no tenant bound.

---

## Next

### GST compliance engine — foundation landed; the integrations remain

**Why this is first.** India's e-invoicing mandate reaches every business above ₹5 crore
aggregate annual turnover from April 2026, and the window for reporting an invoice to the Invoice
Registration Portal is tightening toward three days from the document date. Three days is not a
deadline a person meets by remembering; it is a deadline a system meets. That makes compliance the
platform's chosen primary differentiator — "Compliance Autopilot" — and this the slice everything
else in it stands on.

**Why it is a structural advantage rather than an integration.** Anyone can call the IRP. What is
hard to copy is that Atmora's invoices were already append-only with corrections as credit notes
(ADR-0000 §9), which is *exactly* GST's own legal model: a filed document is never edited, and a
correction is a new document that references it. The two models line up with nothing left over, so
every filing action can be a provable, reversible ledger row instead of a status column somebody
has to trust.

**Slice one — the foundation: DONE.**

- **GSTIN is a value object, not a string.** `domain.parse_gstin` checks length, structure, a real
  state code, and the government's own folded base-36 check digit; it returns a `Gstin` carrying
  the state code, PAN and entity code. Anchored to two public GSTINs, and property-tested: every
  single-character corruption and every adjacent transposition of a valid number is caught.
  Customer GSTINs are verified on write, so this service stores no unverified one.
- **The seller's identity is a finance-side config row.** `gst_registrations` — per state, one
  primary, with `state_code` *derived from* the GSTIN rather than entered beside it. A partial
  unique index makes "at most one primary" a database guarantee.
- **Documents carry their tax facts.** Place of supply, supply type (B2B/B2C/export/SEZ),
  reverse-charge, both parties' GSTINs as snapshots, and per-line HSN/SAC plus per-line rate.
- **The split is integer arithmetic that foots exactly.** `domain.split_gst` computes the tax
  once and then divides it — CGST+SGST intra-state (odd paisa to SGST, in both signs), IGST
  inter-state, with export and SEZ always inter-state by statute. Rounding each half
  independently is the bug the shape exists to prevent. Property-tested across every band and a
  range of amounts from one paisa past the 32-bit boundary; a credit note reverses an invoice
  head for head.
- **`einvoice_filings` is append-only at the database.** One row per attempt — `pending`, `filed`,
  `failed`, `cancelled` — never mutated, so a document's filing history is a query rather than an
  archaeology exercise. Enforced by a `BEFORE UPDATE OR DELETE` trigger, because a privilege
  `REVOKE` does not survive `apply_schema_grants` re-granting DML after every migration run; the
  platform learned that on payment's `refunds` and it is the same shape here. Under RLS like
  every other tenant table.
- **The portal is a `Protocol` with a deterministic stub.** `IrpClient` speaks the document; the
  stub computes the **real** IRN (`SHA-256(supplier GSTIN + doc type + doc number + FY)`) and
  refuses documents the way the portal does, with numbered error codes. No network, no
  credentials — the same bargain payment's Razorpay adapter strikes, so the state machine and the
  persistence are real and testable today and only the wire is left.
- **Endpoints.** Filing status on the invoice read model (`einvoice`, never null, so an unmet
  obligation is visible rather than absent), `GET /finance/invoices/{id}/filings` for the history,
  `GET`/`PUT /finance/gst/registration`, and internal `POST /internal/v1/einvoice/filings` and
  `…:cancel` for the auto-filing agent. New permissions `finance.compliance.einvoice.read` and
  `finance.compliance.registration.read/write`, the last marked dangerous.
- **Events.** `finance.compliance.invoice.filed`, `.filing_failed` (carrying `retryable`, so an
  outage is retried and a rejection is not) and `.filing_cancelled`. `finance.billing.invoice.issued`
  now carries `requires_einvoice` and `place_of_supply`, so an agent can decide from the event
  alone without reading back.
- The demo book is GST-complete: a Maharashtra seller registration, checksum-valid customer
  GSTINs, HSN/SAC on every line, and both split shapes exercised on real data.

**Still open, in the order the market forces it:**

| Next | Why it is next |
|---|---|
| **IRP / GSP integration** | The one stubbed seam. `IrpClient` is the whole change surface; NIC direct vs a GSP is a credentials-and-envelope decision, not an architecture one. |
| **The auto-filing agent** | What makes it "autopilot" rather than a button. Consumes `finance.billing.invoice.issued`, files inside the reporting window, retries only what is retryable. Needs the scheduler and a backoff policy. |
| **IMS reconciliation** | GSTN's Invoice Management System turns a supplier's filings into a recurring accept/reject queue on the buyer's side. This is a *recurring* workflow, which makes it stickier than filing — and it is the natural second product surface. |
| **GSTR-1 preparation** | Falls out of the data this slice now stores: the HSN summary groups by `hsn_sac` and rate, and the B2B/B2C/export tables by `supply_type`. Mostly reporting, once the filings are trustworthy. |
| **e-Way bills** | A different portal with a different obligation (movement of goods above ₹50,000) and its own 24-hour-ish cancellation rules. Shares the seller registration and the document, not the client. |
| **GST-TDS: 194Q / 206C(1H)** | Withholding at source on purchases and collection at source on sales, with the buyer/seller threshold interplay that decides which applies. Touches the payment ledger, not the filing ledger. |
| **B2C dynamic QR** | The obligation B2C carries instead of an IRN. Deliberately out of this slice: `requires_einvoice` already answers `false` for it, so it is additive. |

Two things this slice deliberately did **not** do. Historical documents were not back-split into
tax heads: which head a supply was taxed under depends on a seller state that no column recorded
when those documents were frozen, and deriving it now would be inventing a legal fact rather than
recording one. And UIN/OIDAR registrations are refused rather than half-supported.

### HRMS employee-service — slice 1 landed; follow-ups

The Employee Master (`employee-service`, schema `employee`, port 8120) is in as one green
vertical slice: the `employees` table, the `probation → active → on_notice → exited` lifecycle
state machine, the `hrms.people.employee.read/write` permissions with `HR_ADMIN` and `EMPLOYEE`
role templates, scoped reads (OWN/TEAM/LOCATION/ORG), audit + outbox on every write, RLS on the
new schema, and the full negative-authz / cross-tenant test set. Two things are deliberately
left:

- **Sensitive PII is deferred to slice 2.** Date of birth, personal contact, PAN/Aadhaar/UAN/
  ESI, bank details and documents are *not* stored yet — "PII is encrypted and masked by
  default" is load-bearing and platform-core has no PII envelope helper. Slice 2 lands the
  envelope (encryption at rest, masking, `record_sensitive_read` on every unmask) and then adds
  those fields. Do not add any of them before the envelope exists.
- **Register the `hrms` product + role templates with account/product service: DONE** —
  closed as a platform mechanism, not a per-product edit (ADR-0005). Every product's
  `app/catalogue.py` is exported to a committed JSON manifest in `libs/contracts/permissions/`
  (`tools/export_permissions.py`, checked by the `openapi-check` gate row; the `hrms` manifest
  merges the employee and attendance slices), and account-service's consumer installs the
  activated product's manifest on `core.product.activated` / entitling
  `commerce.subscription.*` — permissions join the catalogue, role templates become org-owned
  system roles, and the admin-most template is granted to the org's owners.
  `tools/wire_product.py` drives the same install by hand for an already-activated org.
  Still open from the checklist: **declare plan features** (step 5) — that remains
  product-service seed data.

### End-to-end journey suite — DONE
`make e2e` boots six services (account, product, auth, customer, deal, app-gateway), an outbox
relay and an event consumer as subprocesses on ports the OS allocates, and drives one ordered
journey over real HTTP against real Postgres and RabbitMQ. Nothing is stubbed. ~20s.

The journey: the owner signs in → an administrator is refused activation → the owner activates
CRM → **the relay publishes the event and account-service consumes it** → the manifest installs
→ **every admin role the product ships is granted** → an entitled endpoint returns seeded rows →
the second service answers too → an unentitled product is 402 → another tenant's row is 404 →
every service resolves grants through the current internal-token scheme.

Written against the three defects that reached `main` on 2026-08-25, each invisible to unit
tests because each side stubbed the other:

- **(a) the silent relay stall** — steps 4-5 assert the outbox row reaches `published_at` and
  account-service's `processed_events`. The harness also runs every process with
  `ENVIRONMENT=ci`, which turns `assert_outbox_role`/`assert_app_role` from a warning into a
  startup failure, so a relay handed an RLS-bound role now dies at boot with the reason.
- **(b) version skew** — step 13 asserts every service in the suite answers 200 to a request
  the gateway signed. A service still verifying the old static shared secret cannot.
- **(c) a product spanning two services** — CRM is the only product with two admin templates.
  Step 7 asserts the owner holds both, and step 9 proves it with data from the second service.

Three things the harness itself is careful about, each having already bitten: ports are
allocated rather than fixed (fixed ports made the suite silently drive a stack that was already
running); `DATABASE_URL`, the broker vhost and the Redis db are pinned by the harness and passed
explicitly, so no developer's `.env` can steer it into the shared database; and the gateway is
addressed as `localhost`, because an IP host makes it infer a tenant slug from the Host header.

**Still open:** the suite seeds its owner by setting `Membership.is_owner` directly, because
`add_member` cannot create one and there is no HTTP route for organization creation — the same
gap `tools/demo/promote_owner.py` exists to repair. And `_seed_product.py` promotes `crm` from
`soon` to `available` in the test database, since it is the only multi-service product and
activation rightly refuses a product that has not shipped.

### `product-service`
Product registry, per-org activation, entitlements, marketplace catalogue. Turns the gateway's
entitlement gate from *a claim the token happens to carry* into something backed by a registry.

Start emitting usage events here with `is_billable=false`. Turning on usage-based pricing later
then becomes plan configuration rather than an architecture change, and it costs almost nothing
now.

### `config-service`
Per-org settings, integration secrets, feature toggles.

---

## After that

| Slice | Why it is where it is |
|---|---|
| Platform services — document, notification, audit, scheduler, eventbridge | Every later product gets files, email, audit and cron without building them |
| **Event consumers** | The outbox produces and nothing consumes. Traced flows stay theoretical until handlers exist |
| HRMS rebuild — employee, attendance, leave, payroll, recruit | Fix the known timezone defect **during** the rebuild rather than porting it |
| PulseBoard rebuild + data migration | **Riskiest slice** — live customer data, preserved UUIDs. Needs a *rehearsed* rollback, not a written one |
| CRM greenfield — customer, sales, marketing, support | CRM owns Customer/Contact; PulseBoard's clients migrate onto it |
| Billing — catalog, subscription, metering, billing, payment | **Razorpay adapter first** |
| Ecosystem surface — super-admin console, marketplace, workflow, analytics | |

**Why Razorpay before Stripe.** It is the more constrained provider: mandate caps, a legally
required pre-debit notice, multi-day eMandate activation, no true idempotency key. An interface
designed Stripe-first produces abstractions Razorpay cannot satisfy — discovered after it has
ten call sites.

---

## Known gaps not yet scheduled

- **Internal credentials are now short-TTL signed tokens on both channels** — the
  `/internal/v1` calls mint per-callee tokens, and the gateway's identity-forwarding hop mints
  a per-request token bound to the forwarded user and organization
  (`platform_core.internal_auth`); the raw secret no longer travels and production refuses the
  `dev-only-`/`ci-only-` placeholders. Still open: the signing key is one shared HMAC secret
  (end state is per-service keypairs or mTLS), and no network policy makes `/internal/v1`
  unreachable from ingress.
- **`record_sensitive_read` is wired in**: the employee PII unmask writes an
  `entity.sensitive_read` row in the caller's transaction (tested in
  `services/employee/tests/test_pii.py`), and the account directory's cross-tenant reads leave
  a structured-log trace (`directory.cross_tenant_read`) — an `audit_logs` row is unwritable
  there, since the read predates any organization and the caller is a service, not an actor.
- **No outbox-lag probe.** A stalled relay is silent. Worth adding before RLS lands, so there is
  a baseline to compare against.
- **SAML is not implemented, and neither is just-in-time provisioning.** OIDC now flows end to
  end (see below), and `org_idp_configs` carries the SAML columns it always did —
  `idp_entity_id`, `idp_sso_url`, `idp_x509_cert` — with nothing reading them. Deferred on
  purpose rather than half-built: SAML is a different trust model (signed XML assertions, XML
  canonicalisation and signature-wrapping attacks, IdP-initiated flows with no request to bind
  a response to) and shares almost nothing with the OIDC path but the configuration row. It is
  worth doing when a customer's IdP cannot speak OIDC; Google Workspace and Entra — which is
  most of the Indian mid-market — can.

  `jit_provisioning` on the same table is likewise inert and documented as such in the model. An
  unknown address is refused, because creating a user, a membership and role grants is
  account-service's job and this platform has exactly one door into the directory: an
  invitation. Opening a second one where an external issuer decides who joins and a column
  decides their roles is a bigger decision than a sign-in flow, and it should be made on its
  own.
- **10 of the 18 quality-gate rows have no tooling** — production build, E2E, schema drift,
  visual regression, automated accessibility, performance budget, query plans, SAST and licence,
  dependency boundaries, token lint.
- **`atmora-internal` is not on the shared packages yet.** `atmora-ui` and `atmora-client` now
  hold the design system and the auth/HTTP layer, and all nine `product-*` apps consume them;
  the staff console still carries its own copies. It is a real fork rather than an oversight —
  its palette is a different token family (lifecycle statuses, a monospaced control-panel face)
  and its auth is the staff registry from ADR-0004 P6a, which has no organization selection. It
  joins once that endpoint lands and the operator palette is expressed as a theme over the
  Console tokens rather than a replacement for them.

---

## Branches deliberately not merged

`feat/tenant-chokepoint`, `feat/e2e-journey` and `worktree-feat+tenancy-prerequisites` are
parallel implementations of work `main` already carries. Their tests fail here not because the
behaviour differs but because they are written against a different internal design — a
`tenant_transaction` helper where this has `get_tenant_session`, per-service DSN environment
variables where this rewrites a role name into one URL.

Adopting them would mean adopting that design: a rewrite of working, tested code to arrive at
the same place. The properties they assert are already asserted here against the design that
exists. The useful pieces were taken instead — the audit coercion, the gate tooling, the outbox
role, the invitation-accept fix, and the cross-tenant sweep and query-plan checks.

They are left on the remote rather than deleted, since deleting somebody else's branch is not
this session's call to make.

## Delivered

| Slice | Commit |
|---|---|
| Platform foundation — `platform-core`, dev bootstrap, CI, service generator | `659ea5b`, `90ed777` |
| Marketing website + frontend workspace | `c788ab0` |
| account-service — orgs, units, locations, users, memberships, roles, invitations | `4210187`, `cf858eb` |
| auth-service — credentials, sessions, refresh rotation, MFA, SSO config, keys | `a6e3548` |
| Gateways — `app-gateway` and `admin-gateway` | `ca58de3` |
| Internal API + permission-version bump | `15d6ec0` |
| Single sign-on — OIDC authorization code with PKCE, tenant login | `<pending>` |

Two of those closed defects that would have been outages in production: the gateways shipped
with a **duplicated correlation header** and an **unenforced per-route `staff_only`** flag, and
account-service was **serving none of the internal endpoints** that auth and both gateways call.

### Single sign-on, in one paragraph

`GET /api/v1/auth/sso/start?email=` reduces the address to its domain, asks account-service
which organization **DNS-verified** that domain (`GET /internal/v1/domains/{domain}`), binds the
transaction to that organization, and reads its OIDC configuration — which is the one auth
table under row-level security, so the binding is what makes it readable at all. It returns the
provider's authorization URL carrying `state`, a `nonce` and an S256 PKCE challenge; the state,
nonce and code verifier live in Redis for ten minutes, single-use.
`GET /api/v1/auth/sso/callback` consumes the state atomically (a replay finds nothing and never
reaches the provider), exchanges the code with the verifier, verifies the ID token's signature
against the provider's JWKS along with issuer, audience, expiry, nonce and `email_verified`,
maps the address to a member of that organization, and mints through the **same** `issue_for`
every other sign-in uses — same audience, same `pv`, same claim shape, `amr: ["sso"]` instead of
`["pwd"]`. Per ADR-0000 §1 the domain selects a *provider*, never an organization: the
organization comes from the configuration that provider belongs to, and membership is still
required. The client secret is sealed with the platform PII envelope, never returned by any
endpoint and never logged. Unknown addresses are refused — see the note above on JIT
provisioning. Configuration is `GET`/`PUT /api/v1/auth/sso/config`, behind
`core.identity.sso.read` / `core.identity.sso.write` at ORG scope.
