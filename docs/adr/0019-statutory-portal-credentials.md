# ADR-0019 — Statutory portal credentials (IRP, e-Way Bill, GSTN returns, IMS) live in finance, sealed, under RLS — not in config-service

Status: **accepted** · Date: 2026-09-24 · Implemented by backlog **B5** (slice 5b, "sealed
credentials/sessions"), before any live portal adapter (5d–5f)

Builds on [ADR-0000](0000-phase-0-decisions.md) §10 (shared-schema tenancy, RLS, no cross-schema
foreign keys) and "Also settled" (PII is sealed and masked by default; audit is written in the
same transaction); on [ADR-0002](0002-row-level-security.md) §3 (policy shape); on
[ADR-0007](0007-permission-rollout-to-live-tenants.md) (why a new permission has a rollout cost);
and on [ADR-0015](0015-scheduler-and-not-before-delivery.md) (finance's retry scheduling).
Answers backlog item **O8** and is referenced from
[ADR-0004](0004-commercial-model.md)'s tax decision. Supersedes nothing.

---

## Context

Finance files e-invoices, e-Way bills, GST returns and IMS actions for its tenants. Every portal
adapter today is a deterministic stub behind a `Protocol`:
- `services/finance/app/irp/base.py` and `irp/stub.py`;
- `eway/stub.py`;
- `ims/stub.py`.

No credential is stored anywhere. Backlog B5 wires the live portals (5b sealed credentials and
sessions, 5c NIC crypto, 5d IRP, 5e e-Way, 5f GSP/GSTN session and IMS). Backlog C7 plans a
`config` service for "per-organization settings, secrets and toggles"; `services/config` is still
the 171-line scaffold. So the question was which of the two holds a tenant's portal credentials.

What a portal credential actually is, per tenant and per GSTIN:
- a **login** for the tenant's own registration: an API username and password, and for direct NIC
  access a client id and client secret the taxpayer registered with NIC;
- a short-lived **session** the portal issues from that login: an auth token and, on NIC, a
  session encryption key that encrypts every payload. It expires within hours and is refreshed
  continually, so it is operational state and not configuration.

Facts that constrain the answer:

| Fact | Evidence |
|---|---|
| A GSTIN is a finance row, per organization, per state | `finance.gst_registrations` (`services/finance/app/models.py`) |
| Setting a registration is already a dangerous, ORG-floored permission, held by `FINANCE_ADMIN` | `finance.compliance.registration.write`, `services/finance/app/catalogue.py` |
| The platform already keeps other tenant-supplied secrets **inside the service that uses them**, sealed and never returned | notification's WhatsApp access token and SMTP app password (`services/notification/app/models.py`, "sealed … Never returned by any API", `secret_enc`) |
| `platform_core.pii.seal` / `open_sealed` is the sealing envelope: AES-256-GCM, a versioned byte, rotation through `pii_decrypt_keys` | `libs/platform-core/src/platform_core/pii.py` |
| No network call may run inside a database transaction or while a row lock is held (claim → call → record) | backlog D3; the static guard it adds |

---

## Options considered

1. **Finance's own schema, sealed, under RLS.** Chosen.
2. **`config` service (C7).** Every tenant's secrets for every product would sit in one service,
   and finance would fetch the plaintext over an internal route on every filing.
3. **An external secret store** (HashiCorp Vault, a cloud secrets manager). The right answer at a
   different scale or under a different compliance regime. Here it is a new stateful component to
   run on a box already over its memory budget (ADR-0013 erratum), with nobody to operate it.
4. **Environment variables.** Not possible: the credentials are per tenant and written at run time
   by the tenant's admin.

## Decision

### 1. Two tables in `finance`, both tenant-scoped with forced RLS

- **`gst_portal_credentials`**
  - `organization_id`
  - `gst_registration_id`: an **in-schema** foreign key to `gst_registrations`, `ON DELETE
    CASCADE`. Removing a registration removes its logins in the same transaction.
  - `portal`: `irp`, `eway`, `returns` or `ims`
  - `provider`: `nic`, or the GSP's key
  - `username_masked`, `username_enc`
  - `secret_enc` (the password or client secret)
  - `client_id_enc` (nullable)
  - `rotated_at`, `last_verified_at`, `last_error_code`
  - `UNIQUE (organization_id, gst_registration_id, portal)`
- **`gst_portal_sessions`**
  - `organization_id`, `credential_id` (in-schema FK)
  - `token_enc`, `sek_enc` (nullable)
  - `expires_at`
  - `refresh_lease_until`: the single-flight lease, see §3
  - `UNIQUE (organization_id, credential_id)`

Every secret column is sealed with `platform_core.pii.seal` on write. Only masked forms are ever
read back.

### 2. Written by the tenant's finance admin, under the permission that already governs the registration

`PUT` and `DELETE /api/v1/finance/gst/registration/portal-credentials/{portal}` sit beside the
existing singular `/finance/gst/registration` route, which today addresses the organization's
primary registration. When per-state registrations become individually addressable, the path
gains the registration's id. The routes are guarded by the existing
`finance.compliance.registration.write`, floored at ORG. `GET` returns the masked username,
`last_verified_at` and `last_error_code` under `registration.read`. **No route ever returns a secret.** There is no unmask path at all, so there
is nothing to audit as a human read. `POST …/verify` checks the login with the portal once
(claim → call → record) and records the outcome.

Every set, rotate and remove writes an audit row (`gst_portal_credential.set`, `.rotated`,
`.removed`) in the same transaction. The row never contains the secret, and never its length or
hash.

**No new permission.** Whoever may change which GSTIN the organization files under is exactly who
should set the login it files with. A separate permission would cost an ADR-0007 fleet reconcile
to separate two acts nobody has asked to separate.

### 3. Used only by finance's worker, only in the "call" step, never under a lock

Plaintext exists only in finance's worker process, only for the duration of one portal call, in
the **call** step of claim → call → record. No database transaction or row lock is open while it
exists.

Sessions are refreshed **single-flight**. A caller that finds the session expiring claims
`refresh_lease_until` in a short transaction and commits. It then calls the portal's auth endpoint
with no transaction open, and records the new sealed token in a second transaction. Any other
caller that sees a live lease waits for the recorded session instead of starting its own refresh.
So a burst of filings never stampedes the portal's auth endpoint, and no lock is held across the
network.

Plaintext never appears in a log, an event payload, an error detail or a problem response. A
portal's own error text is recorded only as its numbered code and a sanitised message, the way
`einvoice_filings.error_code` works today.

The worker using a credential is **not** a sensitive read. Nobody sees the plaintext, and every
filing attempt is already recorded append-only.

### 4. Two credentials that are *not* tenant rows, and one that is

- **The platform's own GSP/ASP client credential.** A GSP issues it to OpsGrids as the application
  provider, and it is the same for every tenant. It is a **deployment secret** in the box's `.env`,
  like the Razorpay keys (ADR-0013 §6). Finance reads it from settings.
- **OpsGrids' own login as a taxpayer** is **the operator organization's
  `gst_portal_credentials` row**. It uses the same mechanism as every tenant. This is what
  [ADR-0004](0004-commercial-model.md)'s tax decision relies on when finance files OpsGrids' own
  returns and IRNs under the operator organization.

### 5. What `config` (C7) is for, then

Non-secret per-organization settings and toggles that more than one service reads, and any secret
that **no single service owns**. A secret one service uses to do its own job belongs to that
service, as notification's and now finance's do. C7's scope narrows to match. This ADR is the
reason.

## Why

- **Blast radius.** A compromise of finance exposes finance's secrets. A config service holding
  every tenant's every secret would expose all of them at once.
- **The filing path has deadlines.** Fetching a secret synchronously from another service on
  every filing adds a dependency whose outage misses a statutory deadline. With the secret in
  finance's own schema, it is read in the same process that files.
- **Lifecycle.** The credential belongs to a registration row. An in-schema foreign key with a
  cascade keeps them consistent for free. In another schema it would be a cross-schema reference,
  which ADR-0000 §10 forbids enforcing, so the two would drift.
- **No plaintext crosses the network.** Option 2 would need the platform's most sensitive internal
  route: one that returns plaintext secrets to a caller holding a service token.
- **Precedent.** Notification already does exactly this for WhatsApp and SMTP secrets. One pattern
  is cheaper to review than two.

## Consequences

- Finance's schema holds ciphertext for every tenant's portal logins. The root of trust is the PII
  key, which is platform-wide today. Backlog **P3** (a per-service key derived with HKDF, and AAD
  bound to organization, key and version) narrows it so that finance's secrets are sealed under a
  key only finance derives. Re-sealing these rows is part of P3's migration.
- A database dump or backup without the key reveals nothing. Rotating the key is operational
  (`pii_decrypt_keys`), not a data migration.
- Code execution inside finance exposes every tenant's portal logins. That is true of any service
  holding credentials, and it is why these secrets are not also in a second service.
- One background concern is added: expired sessions are refreshed lazily, on use, not by a sweep.
- **Implemented by B5 (5b).** The live adapters 5d–5f read credentials only through this path.

## Revisit when

- **P3 lands.** Re-seal both tables under finance's derived key, with AAD of
  `(organization_id, gst_registration_id, portal)`, so a ciphertext copied to another row fails to
  open.
- **A compliance regime or a customer contract requires HSM- or KMS-backed secret storage.** Then
  option 3's envelope key moves to a KMS, and `pii.seal` gains a new version byte. The tables and
  the flow do not change.
- **A second service needs the same credential**, for example if IMS moved out of finance. Then
  the owner of the registration serves it through claim → call → record on its own side. It never
  hands out plaintext.
