# ADR-0023 — Member step-up: prove a one-time code, get a short-lived `otp` token

Status: **accepted (built)** · Date: 2026-09-27 · Service: `auth`

Builds on [ADR-0000](0000-phase-0-decisions.md) §2 (only `auth-service` mints tokens), §3 (an access
token carries no permission list) and the 2026-09-17 note under §5 (`is_dangerous` is metadata, not
a control); [ADR-0003](0003-surfaces-and-authorization-topology.md) (the gateways verify, the
services authorize). The guard it feeds is `platform_core.auth.require(perm, amr={"otp"})`.

**Amended 2026-09-27 after the independent security review** (finding B1 and its follow-ups): the
first version of this ADR added the step-up endpoint and left enrolment as it was, which let a
stolen bearer token enrol its own authenticator and pass the step-up with it. The enrolment gate,
the session binding and the step-up factor rule below close that; "Risks accepted" records the
attack, and two claims the first version overstated are corrected in place.

---

## Context

The "reveal" routes hand out data that is masked everywhere else: a CRM contact's or lead's phone
number (`crm.book.contact.read_sensitive`), an employee's PII (`hrms.people.employee.read_sensitive`),
a storefront enquiry (`storefront.sales.enquiry.unmask`) and an Engage inbox message
(`engage.inbox.message.unmask`). Each holds a permission **and** demands `amr={"otp"}` at the route.
The permission says the role may; the `otp` requirement says this token proved a one-time code.

That requirement could not be met by an ordinary member:

- Every token is minted with `amr` taken from the persisted `Session.amr` (`issue_for`, and the
  grace-window path of `rotate_refresh`), so `otp` is **sticky for the life of a session**.
- A member session got `otp` in exactly one place: `verify_mfa`, the second step of *login*. A
  member who enrols an authenticator on the dashboard Security page after signing in, or whose
  session started before they enrolled, has no way to obtain it short of signing out and in again.
- An organization administrator cannot enforce MFA for members, so most members never see the
  login step at all.
- A session started through single sign-on can neither enrol a factor (`_require_primary_session`)
  nor carry `otp` (`SSO_AMR`, deliberately not `pwd`).

The result was a permission that could be granted and a route that could never be passed. The
comments beside those routes described "a recent second factor"; the guard has never measured
recency, only presence of the claim.

## Decision

### A. `POST /api/v1/auth/mfa/step-up`

Body `{code}`, bearer-authenticated. It verifies a TOTP code for the signed-in member and returns
the existing `TokenOut` (`access_token`, `token_type`, `expires_in`, plus the organization and
membership ids) — an **access token whose `amr` is the session's `amr` plus `otp`**.

1. **Identity comes from the verified bearer token, never the body.** The session is the one the
   token's `sid` names and must belong to its `sub`; the organization is the token's `act_org`, and
   the member must still hold an active membership in it. The body schema rejects unknown fields.
2. **TOTP only.** A recovery code is a single-use break-glass credential; burning one to reveal a
   phone number is a trade the member never agreed to. A recovery-shaped input is refused (422)
   before anything is read or counted, and `_accept_second_factor(allow_recovery=False)` removes
   the recovery loop from the verifier for this caller, so it cannot be spent even by a future
   caller that forgets the input check.
3. **Same counter, same lock, same replay guard as login.** The credential row is locked
   (`SELECT … FOR UPDATE`) before the code is checked, so concurrent guesses — and concurrent
   submissions of one correct code — serialise. Failures increment `Credential.mfa_failed_attempts`;
   the fifth locks `mfa_locked_until` for `lockout_minutes`, restarts the count, and **revokes the
   session and its refresh tokens** (reason `step_up_failed_attempts`), through the one helper
   (`_register_mfa_failure`) `verify_mfa` now also uses. A code already spent for its 30-second
   step (`MfaFactor.last_used_step`) is refused as a replay.
4. **The result is an access token and nothing else.** No refresh token, no `RefreshToken` row, no
   cookie. `otp` goes into **this token's** `amr` only (`_mint_access_only(extra_amr=("otp",))`) and
   is **never written to `Session.amr`**, so neither a refresh nor a rotation can carry it into a
   later token. It lives for `step_up_token_ttl_seconds` (default 180, validated to 30–600; startup
   fails outside that range, and it is never longer than an ordinary access token).
5. **Audited.** `auth.step_up_verified` and `auth.step_up_failed` carry the user, session id, IP,
   user agent and request id. The submitted code is never an input to an audit row (the `after`
   payload is built from a fixed set of outcome fields, and a test pins that set).
6. **Bare session with explicit commits, tenant bound first.** A wrong code has to be counted and
   the lockout it trips has to stick even though the request answers an error; the tenant
   chokepoint (`get_tenant_session`) rolls back on an exception. So the route takes a bare session
   like `mfa_verify`, and the service binds the tenant GUC itself (`_bind_tenant`: `set_config`,
   then the read-back probe outside production) as the first statement it runs. The directory and
   entitlement calls come **before** that, so a slow upstream never holds a pooled connection or
   the credential row lock. The route audit in `tests/test_tenancy.py` carries an explicit,
   reasoned exemption for it and a behavioural test that the binding happens first even when the
   request is refused.
7. **A factor must predate the session, or have been enrolled through it.** `step_up` only checks
   a code against a confirmed factor that satisfies one of two comparisons
   (`_factor_usable_in_session`): `confirmed_at <= session.auth_time` — every sign-in that could
   have required it happened with it in place — or `enrolled_session_id == session.id` — it was
   enrolled through this very session by the password-gated `begin` below. A member whose only
   factors fail both gets `403 mfa-reauthentication-required` (uncounted, code not compared) and
   is asked to sign in again. In a mixed account the code of an unusable factor is just a wrong
   code for the usable ones.

### B. Enrolment is gated on the password (B1)

`POST /api/v1/auth/mfa/enrol` and `/enrol/confirm` used to need only a bearer token: no password, no
proof of an existing factor; a second factor could be added while one was confirmed; confirming
deleted the member's recovery codes and handed a fresh set to the caller; no organization audit row
was written; and the session check never asked whether the session was live. Now:

1. **`begin` re-proves who is asking.** Body `{password, current_code?}` (unknown fields refused).
   - The **current password** is always required, verified with the sign-in verifier and counted
     against the **same** `failed_attempts` / `locked_until` as sign-in (`_register_password_failure`,
     now shared by both). A wrong one is `422 password-incorrect`, never 401 (see the error table).
   - When the account **already has a confirmed factor**, `current_code` is required too: a TOTP
     code from it, or a recovery code (which is then spent, and audited as
     `auth.mfa_recovery_code_used`). A missing one is `422 mfa-current-code-required` before the
     password is even hashed and counts nothing; a wrong one counts against the same
     `mfa_failed_attempts` / `mfa_locked_until` as login MFA and step-up.
   - The password is checked **before** the code, so a wrong password never spends a code.
   - Either lockout tripping **ends the session** the guesses came from (reason
     `enrolment_failed_attempts`). A locked account is refused (`403 password-locked` /
     `403 mfa-locked`) without counting.
   - A member with no factor has only the password to offer; for the first factor that *is* the
     trust anchor, and there is nothing more to ask.
   - SSO sessions are refused outright (`403 mfa-unavailable-for-sso`, before the password is
     looked at), as is any session that is not live (`401`).
2. **`confirm` is bound to the session that began.** `begin` records `MfaFactor.enrolled_session_id`
   (migration `5d2f8a1c7e94`, nullable, no foreign key). `confirm` requires a live password session
   of the same person **and** `enrolled_session_id == that session`; anything else — another
   session of the same member, a pending factor created without a session, another person's factor,
   a missing one — is the same `404 No such enrolment`. Before, `confirm` was bound only to the
   user and to an unguessable factor id.
3. **Only the first factor mints recovery codes.** Confirming a second factor leaves the existing
   codes exactly as they were and returns `recovery_codes: []`. The one exception is replacing a
   legacy plaintext factor this deployment cannot read: with nothing usable left the new factor is
   the first, and the old codes are replaced with it. There is no regenerate endpoint; if one is
   ever added it must be an explicit, re-authenticated operation of its own.
4. **Every step is audited against the organization** (actor, session id, IP, user agent, request
   id; never a password, code, secret or recovery code): `auth.mfa_enrolment_started`,
   `auth.mfa_enrolment_failed` (with `reason` and `locked`), `auth.mfa_enrolment_confirmed`,
   `auth.mfa_recovery_codes_issued` (a count and `replaced_previous`, not the codes) and
   `auth.mfa_recovery_code_used`.
5. **The notification hook that exists is `core.user.mfa_enrolled`.** It is published to the outbox
   on every confirmation (payload now also `first_factor` and `recovery_codes_issued`; the nil
   organization is unchanged, a credential belongs to a person). It is on notification's
   `SUBSCRIBABLE_EVENTS`, so a rule *can* email the member, but **no rule or template is bound to
   it** — the only platform-tenant rule today is the password reset. So no email is sent when an
   authenticator is added. Adding one (a `platform-mfa-enrolled` rule and template, modelled on
   migration `b8c1d2e3f4a5`) is a notification-service change and is not part of this ADR.
6. **`enrol` and `confirm` responses are `Cache-Control: no-store`**: they carry a TOTP seed and
   recovery codes.
7. **Bare session for `begin`, tenant chokepoint for `confirm`.** `begin` counts failures, so it
   takes a bare session, commits for itself and binds the tenant first (a second reasoned entry in
   the tenancy route audit). `confirm` counts nothing — a wrong code is checked against the pending
   factor's own secret, which only the enrolling session has seen — so it stays on the chokepoint.

### C. `switch-org` to the organization you are already in is refused

`POST /auth/switch-org` is the one route that exchanges a bearer access token for a refresh token
(a cookie), so it is the cheapest way for anyone holding an access token to outlive it. Switching to
the organization the presenting token is already for changes nothing and has no legitimate caller
(the dashboard's switcher offers only the other organizations and shows "Current" for this one), so
it is `409 already-in-organization`, checked after the federated-session re-validation so a dead
SSO session is still ended rather than merely refused. See risk 8 for what this does not close.

### Error contract (RFC 7807 `type` is `https://errors.opsgrids.dev/<slug>`)

| Status | Slug | Where | Meaning |
|---|---|---|---|
| 401 | `unauthorized` | all | No/invalid/expired bearer, the session is revoked, expired or not yours, or the membership has ended. Nothing counted. |
| 403 | `mfa-unavailable-for-sso` | enrol, confirm, step-up | The session came from an identity provider. Needs a password sign-in. |
| 403 | `password-locked` | enrol | Sign-in's password lockout. |
| 403 | `mfa-locked` | enrol, step-up | Too many wrong codes; same message as the login lockout. |
| 403 | `mfa-reauthentication-required` | step-up | The authenticator was added after this session began and not through it. Sign in again. |
| 404 | `not-found` | confirm | No such enrolment: missing, not yours, or begun by a different session. |
| 409 | `mfa-not-enrolled` | step-up | No confirmed factor. Send the member to the Security page. |
| 409 | `already-in-organization` | switch-org | The token is already for that organization. |
| 422 | `password-incorrect` | enrol | Wrong password. Counted. `errors[0].field` is `password`. |
| 422 | `mfa-current-code-required` | enrol | The account has an authenticator: send `current_code`. Not counted. `errors[0].field` is `current_code`. |
| 422 | `mfa-code-invalid` | enrol, confirm, step-up | Wrong code. Counted at enrol and step-up, not at confirm. `errors[0].field` is `code` (`current_code` at enrol). |
| 422 | `mfa-code-replayed` | enrol, step-up | A valid code for a step already spent (typically the one that just signed them in). Counted. Says to wait for the next code. |
| 422 | `validation` | all | Malformed body, unknown field, or (step-up) not six digits, including a recovery code. Not counted. |
| 503 | `mfa-reseal-pending` | enrol, step-up | A legacy factor awaiting `bin/reseal-mfa-factors`. Not counted. |

A wrong password or code is **422, not 401**, on purpose. The shared client answers a 401 on a
bearer-authenticated call by refreshing the token and re-sending the same request, then signing the
user out if that fails: a mistyped value would be counted twice and end the session. With 401
reserved for a bearer or session that is actually bad (nothing has been counted when it is raised),
the client's default refresh-and-retry is the right behaviour for it. (`confirm` used to answer a
wrong code with 401, which does exactly that; it is 422 now.)

## Alternatives rejected

- **Write `otp` into `Session.amr` on success.** Simple, and it is what login does — but the
  session is what refresh mints from, so one code would unlock every reveal route for up to seven
  days, on every device sharing the session. The whole point of a step-up is that it ends.
- **Also mint a refresh token so the step-up can be renewed.** A second long-lived credential whose
  only purpose is to keep a privilege alive; the reveal is a moment, not a mode.
- **Accept recovery codes at step-up.** They are finite and irreplaceable until re-enrolment.
  (They *are* accepted as the "current code" when adding a factor, where spending one is the
  member's explicit choice and the alternative is being unable to enrol at all.)
- **A separate lock counter for step-up.** Two counters against one secret double the attacker's
  guesses, and a tidy-looking separation is not a defence.
- **Bind confirm to the begin with a cache entry rather than a column.** Redis is not durable, and
  the step-up rule needs the binding for the session's whole life, not for ten minutes.
- **Add a `require(... amr=...)` freshness check (`otp_at`) in `platform-core`.** Right in the long
  run (below) but it redeploys every service; this slice changes only `auth`.

## Risks accepted

1. **A step-up token cannot be ended early.** Access tokens are verified by signature at the
   gateway, which checks the route, the token, the permission version and the entitlement — and
   **never the session** (`platform_core.gateway`, the documented order of checks). So logout, a
   lockout, a password change, a session revoked by any means and a deactivated account do **not**
   stop a step-up token that has already been issued; only a permission-version bump (any change to
   the organization's grants) makes it stale. It lives for at most `step_up_token_ttl_seconds`
   (default 3 minutes; bounded at 600 for exactly this reason). *The first version of this ADR
   said the session's ending "acts on" it. It does not.*
2. **A step-up token can be chained with a fresh code, using itself as the bearer.** It is a valid
   access token with a `sid`, so `POST /auth/mfa/step-up` accepts it and a fresh code (one for a
   step not yet spent) yields another. It has no refresh token, but it is renewable **for as long
   as the session is live and someone can produce a code** — which is the point of the check, and
   is bounded by the session ending, since `step_up` reads the session. The first version said it
   "cannot be renewed"; that was wrong.
3. **Lockout is coupled across the doors.** Step-up, enrolment and login MFA share one
   `mfa_failed_attempts` / `mfa_locked_until`; enrolment and sign-in share one
   `failed_attempts` / `locked_until`. Someone holding a stolen bearer token can therefore lock the
   real member out of **login** for `lockout_minutes` (default 15) by guessing wrongly, and the
   attempt that trips a lock ends the session they are guessing from. This is the same exposure the
   login form already has and there is deliberately no admin unlock; expiry is the unlock.
4. **The token's scope is every reveal route, not one record.** It carries `otp`, and every
   `require(..., amr={"otp"})` route across CRM, HRMS, the storefront and Engage accepts it until it
   lapses. The holder still needs the permission, the scope and (for record-level reveals) the
   object-level check, and every reveal writes its own `entity.sensitive_read` audit row — but one
   code opens all of them for the window, not just the one the member had in mind.
5. **No freshness for sessions that already carry `otp`.** A session that presented a code at login
   has `otp` in `Session.amr` and so in every token it ever mints. The guard checks presence, not
   age, so for those sessions "step-up" is satisfied for the session's whole life. This slice does
   not change that. **Phase 2:** stamp `otp_at` into the token (and out of `Session.amr`), and have
   `require(..., amr=..., max_age=...)` refuse a token whose `otp_at` is older than the route
   allows. That is a `platform-core` change and redeploys every service; it needs its own release.
6. **Single sign-on members cannot reveal.** A federated session is refused (`mfa-unavailable-for-sso`)
   and an SSO-only account has no password to sign in with instead. Making that reachable needs a
   decision about whether an external issuer's own MFA claim (`amr`/`acr` in the ID token) may
   satisfy `otp`, which is a policy question this ADR does not settle.
7. **Entitlements degrade to empty.** As at login and refresh, an unreachable product-service
   yields a token with no `ent`, and the entitlement check on the reveal route then refuses (402)
   while the code has already been spent for its step. The member retries with the next code.
8. **A bearer token can still become a refresh token for a member of two or more organizations.**
   `switch-org` now refuses the organization the token is already in, which closes it for a member
   of one organization (the large majority). A member of two can still be moved to the other and
   back, and each move sets a fresh refresh cookie. Closing it properly means `switch-org` must
   present the refresh cookie the session already holds (the cookie's path, `/api/v1/auth`, already
   covers the route) and the CSRF header, so a bearer alone gets nothing; that changes the
   dashboard's call and is a follow-up. No other route mints a refresh token from a bearer:
   `login`, `select-org` and `mfa/verify` need a password or pre-auth token, `refresh` needs the
   cookie, SSO needs the identity provider, and operator sign-in has its own gates.
9. **The password is now the trust anchor for adding a factor, and `password/change` does not
   count wrong guesses.** `POST /auth/password/change` verifies the current password with no
   counter and no lockout check, so a bearer holder can test candidate passwords through it at
   whatever rate the gateway allows, and enrolment's password gate is only as strong as that.
   Pre-existing and not changed here (it runs on the tenant chokepoint, which would need the same
   bare-session treatment as `enrol`); the fix is to call `_register_password_failure` there.
   Recorded as a follow-up, not a mitigation. (Its wrong-password answer is also a 401, so the
   shared client refreshes, re-sends and signs the member out — the reason enrolment answers 422.)
10. **Sessions that enrolled before this release need a fresh sign-in to step up.** A factor
    confirmed inside a session that is still open across the deploy carries no
    `enrolled_session_id` and was confirmed after that session began, so it is refused
    (`mfa-reauthentication-required`) until the member signs in again. Sessions last at most
    seven days and the answer is one sign-in; a pending enrolment begun before the deploy has to be
    started again. Both fail closed.
11. **The attack this amendment closes (B1), for the record.** *A stolen bearer token could enrol
    its own authenticator and then pass step-up.* Steps: take a member's access token (an XSS
    payload, a leaked log line, a shared machine); `POST /auth/mfa/enrol` — which needed no
    password — and scan the seed into an app the attacker holds; `POST /auth/mfa/enrol/confirm`
    with the code, which deleted the member's recovery codes and returned new ones; wait one 30 s
    step; `POST /auth/mfa/step-up` with the attacker's code, which accepted *any* confirmed factor;
    reveal every masked field the token's permissions reach; repeat. `amr={"otp"}` protected
    nothing against bearer theft. **Fix:** part B (password, plus a code from any existing factor;
    confirm bound to the beginning session; live session; recovery codes preserved; audited) and
    part A.7 (step-up only spends a factor that predates the session or was enrolled through it).
    **Residual:** an attacker who has the token *and the password* (and, for an account with a
    factor, a code from it) can still enrol — but then holds the credentials that make the token
    unnecessary. A member with no factor and a phished password is exactly as exposed as their
    password: for the first factor the password is all there is.

## Who can reveal, really

The step-up decides whether a token *may* reveal; the grants decide who *holds* the permission.
Read together, so nobody assumes the second is narrower than it is:

Read from the role catalogues (`services/*/app/catalogue.py`) and the route guards:

| Permission | Route guard | Scope floor | Roles that hold it |
|---|---|---|---|
| `crm.book.contact.read_sensitive` | `require(..., amr={"otp"})` | none | `CRM_BOOK_ADMIN` at `ORG` (built from `.all("ORG")`); `CRM_SALES_REP` (the default role) at `OWN`, added by the reveal release. |
| `crm.lead.lead.read_sensitive` | same | none | `CRM_LEADS_ADMIN` at `ORG` (built from `.all("ORG")`); `CRM_LEAD_REP` (the default role) at `OWN`. New in the reveal release, together with the permission itself. |
| `hrms.people.employee.read_sensitive` | same | none | `HR_ADMIN` at `ORG`; `EMPLOYEE` at `OWN` (their own record) |
| `storefront.sales.enquiry.unmask` | same | `ORG` | `STOREFRONT_ADMIN` only; `STOREFRONT_STAFF` holds no enquiry permission |
| `engage.inbox.message.unmask` | same | `ORG` | `ENGAGE_ADMIN` only; `ENGAGE_INBOX_AGENT` is explicitly without it |

- **`CRM_LEADS_ADMIN` is built from `CRM_LEADS.all("ORG")`, so it now includes
  `crm.lead.lead.read_sensitive:ORG`.** Every CRM owner and administrator can therefore reveal
  **any** lead's email and phone in the organization. That is intended — it is the same as
  `CRM_BOOK_ADMIN` for contacts — but it means the step-up is the *only* thing between an admin
  account and every lead's contact details, and the audit row is the only trace.
- **`CRM_LEAD_REP`'s and `CRM_SALES_REP`'s `OWN` grants match nothing today.** `OWN` scope resolves
  to "records whose owner is my `subject_ref`", and `subject_ref` (the `sref` claim) comes from the
  membership's employee link, which does not exist until ADR-0008's link is built and populated.
  With `subject_ref` unset, `ScopeContext.apply` matches no rows and `assert_can_touch` refuses, so
  those reps reveal nothing until then. Do not read "reps hold `read_sensitive:OWN`" as "reps can
  reveal their own leads or contacts".
- **Two corrections to the first version of this ADR** (both are in "Risks accepted" above): no
  step-up token can be ended early — logout, lockout, password change and deactivation do not,
  because the gateway never checks the session — and it can be chained with a fresh code using the
  step-up token itself as the bearer.
- `is_dangerous` on these permissions is metadata, not a control (ADR-0000 §5 note). What stops a
  reveal is the scope floor on the route, the grant, the `otp` requirement and the audit row.

## Revisit when

- `otp_at` and a `max_age` on `require()` ship (Phase 2, risk 5): the TTL setting then becomes the
  ceiling on a token, and a per-route maximum age becomes the real control.
- An organization gains the ability to enforce MFA for its members: the login step then carries
  `otp` for every session and this endpoint becomes the exception path.
- Federated members need to reveal (risk 6).
- Revocation of individual access tokens is built (the gateway learns to check the session): risks
  1 and 2 go away.
- `switch-org` is moved onto the refresh cookie (risk 8), and `password/change` counts failures
  (risk 9).
- A notification rule for `core.user.mfa_enrolled` is added, so a member is told when an
  authenticator is added to their account.

## Deploy checklist

The reveal release: member step-up (auth), lead and contact reveal (lead, customer), the dashboard
Security page and the CRM Reveal buttons. **Not deployed; the owner approves the backend deploy
separately.** In order:

1. **Migration.** auth `5d2f8a1c7e94` (`auth.mfa_factors.enrolled_session_id`, nullable UUID, no
   foreign key; down revision `f8e432a62a84`, the single head). `make deploy` migrates before it
   recreates containers (preflight, build, caddy validate, **migrate**, recreate, health), so the
   column is there before the new auth serves; it is additive, so the old auth keeps working
   against it meanwhile. No manual step. lead and customer have no migration. `remote.sh migrate`
   also ends with the existing MFA reseal (`lib/mfa-reseal.sh`), a no-op unless a legacy plaintext
   factor is left; until such a factor is resealed, enrol and step-up answer 503
   `mfa-reseal-pending`.
2. **Environment.** Nothing new is required and `preflight.sh` has nothing new to check. Optional,
   defaults correct for production and not plumbed through compose: `STEP_UP_TOKEN_TTL_SECONDS`
   (auth, 180; startup fails outside 30-600) and `REVEAL_BURST_LIMIT` / `REVEAL_BURST_WINDOW_SECONDS`
   / `REVEAL_DAILY_LIMIT` (lead and customer, 30 / 600 / 200; the two share Redis keys, so keep them
   equal if ever set). `REDIS_URL` already reaches `lead` and `customer` through the shared
   `x-service-env` block and both wait for redis to be healthy; `tests/infra/test_compose_reveal_redis.py`
   pins it.
3. **Redis becomes a dependency of two more services.** lead and customer open a client and spend a
   per-user reveal budget before every reveal. Redis down: the reveal routes answer 503 (fail
   closed), `/health/ready` on those two reports `degraded` (still HTTP 200, no restart), and
   nothing else in either service changes.
4. **Permission catalogue: reconcile `crm`, after the deploy is healthy, never as part of it.**
   New dangerous permission `crm.lead.lead.read_sensitive` (default `OWN`, max `ORG`);
   `CRM_LEADS_ADMIN` gains it at `ORG`, `CRM_LEAD_REP` at `OWN`; `CRM_SALES_REP` gains
   `crm.book.contact.read_sensitive` at `OWN`. Existing organizations get none of it until:

   ```bash
   infrastructure/edge/remote.sh reconcile-permissions crm            # dry run: read the diff first
   infrastructure/edge/remote.sh reconcile-permissions crm --apply    # fleet write
   ```

   (`make edge-reconcile HOST=... CONFIRM=... PRODUCTS=crm [APPLY=1]` is the same.) `--apply` widens
   roles (union only) and bumps `pv` in every affected organization, so every signed-in member
   refreshes their token: run it outside peak hours. Every CRM owner and administrator can then
   reveal any lead or contact in their organization, behind the step-up.
5. **Reps must enrol an authenticator first**, on the dashboard Security page (their password, then
   a code from the new app). Someone who enrols inside their current session can step up in it; one
   whose authenticator was added in another still-open session, or before the deploy, gets
   `mfa-reauthentication-required` and signs in once (risk 10). Reps' `OWN` grants match nothing
   until the ADR-0008 employee link exists (see "Who can reveal, really"), and single sign-on
   members cannot reveal at all (risk 6), so at first the people who can are `ORG` holders with a
   password sign-in.
6. **Frontends go with the API.** Publish the dashboard and the CRM app (`APP=dashboard` and
   `APP=crm` with `publish-web.sh`, as the last release did). The old dashboard cannot enrol against
   the new auth (`mfa/enrol` now needs `password`, so it answers 422), and the CRM Reveal buttons
   need the new lead, customer and step-up routes; publish after the API is healthy and do not
   leave the two apart.
7. **Known weakest link: `POST /auth/password/change` counts no wrong guesses** (risk 9). Enrolment's
   password gate is only as strong as that route, and a bearer holder can test candidate passwords
   through it at whatever rate the gateway allows. Pre-existing and not changed here; the fix is to
   call `_register_password_failure` there, on the bare-session pattern `enrol` uses. Do not read
   step-up as protection against someone who holds both a stolen bearer token and time to guess.
8. **One oddity to know, not to fix now.** `enrol/confirm` on a factor that is already confirmed
   answers 401, which the shared client reads as a dead session (refresh, retry, sign out). The
   dashboard cannot send it twice, but a lost response followed by a manual retry would. A 409
   would be kinder.
