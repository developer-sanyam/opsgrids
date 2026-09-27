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

**Amended again 2026-09-27 after a second independent review** (of `168027cf`): (1) a session that
gave a code **at sign-in** carries `otp` for its whole life, so `amr={"otp"}` did not distinguish
it from a step-up and every session opened after enrolment could reveal with no code — the reveal
routes in lead and customer now also require a marker only the step-up mints (A.8); (2)
`POST /auth/password/change` was an unthrottled password oracle in front of the trust anchor for
enrolment — it is now guarded like sign-in (Part D); (3) confirming an enrolment could be guessed
at and a pending factor never expired — it now expires, is counted, and cannot outlive the first
working factor (B.8). Risk 5 (the "edge case") and risk 9 (the "weakest link") are rewritten
accordingly.

**And a third time, after that round's own independent review found no blocker**: the password
failure count now restarts with the lock, so the lock expiring is a real unlock (Part D.2, risk 9);
the reveal budget is one atomic Redis step under one timeout (checklist 3); a successful password
change is audited against the member's organization like its failures (Part D.4); the OpenAPI text
for the refusals is corrected; and the three unmask routes that still take `otp` alone are now
named and pinned by tests (risk 5, checklist 9).

---

## Context

The "reveal" routes hand out data that is masked everywhere else: a CRM contact's or lead's phone
number (`crm.book.contact.read_sensitive`), an employee's PII (`hrms.people.employee.read_sensitive`),
a storefront enquiry (`storefront.sales.enquiry.unmask`) and an Engage inbox message
(`engage.inbox.message.unmask`). Each holds a permission **and** demands `amr={"otp"}` at the route.
The permission says the role may; the `otp` requirement says this token proved a one-time code.

That requirement could not be met by an ordinary member:

- Every token is minted with `amr` taken from the persisted `Session.amr` (`issue_for`, and the
  grace-window path of `rotate_refresh`), so `otp` is **sticky for the life of a session** —
  which is also why `otp` alone cannot say "a code was proved *for this reveal*" (A.8).
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
   cookie. `otp` and the step-up marker (A.8) go into **this token's** `amr` only
   (`_mint_access_only(extra_amr=("otp", STEP_UP_AMR))`) and are **never written to
   `Session.amr`**, so neither a refresh nor a rotation can carry them into a later token. It lives
   for `step_up_token_ttl_seconds` (default 180, validated to 30–600; startup fails outside that
   range, and it is never longer than an ordinary access token).
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
8. **The marker: `otp` alone is not a step-up.** Confirming a factor turns MFA on for the account
   (`mfa_enforced`), so **every later sign-in goes through `verify_mfa`, which writes `otp` into
   `Session.amr`** for the session's whole life (up to seven days). Every access token that session
   ever mints already passes `require(..., amr={"otp"})`: a stolen *session* token would reveal with
   no code presented. So the token this endpoint mints also carries `stepup` in its `amr`
   (`STEP_UP_AMR`), a value `verify_mfa`, `issue_for`, refresh, organization switch and the
   grace-window rotation can never produce — `_session_amr` strips it from whatever a session row
   says, so even a contaminated row cannot mint it — and the lead and customer reveal guards are
   `require(REVEAL_PERMISSION, amr={"otp", STEP_UP_AMR})`. `require` demands **every** member of the
   set (`amr.issubset(...)`), pinned by test against tokens carrying each half alone. The literal is
   duplicated across the three services rather than shared, because a shared constant lives in
   `platform-core` and changes every service's image; `tests/infra/test_step_up_marker.py` pins the
   copies equal and pins, as rules over the source, that the literal appears only at its three
   definitions, that `STEP_UP_AMR` is named only in `step_up`, `_session_amr` and the two guards,
   that every token mint and every write to a session's `amr` (the operator flows included) is
   built from `_session_amr(...)`, and that no session is built or bulk-updated with an `amr` of
   the caller's choosing — so "no mint path can produce it" is a property of the source and not
   only of the paths a test thought of. **Scope: the CRM routes only.** The
   employee, storefront and Engage unmask routes still require `otp` alone, because their UIs do
   not run the step-up yet (risk 5).
   Every reveal audit row records the token's `amr` (`after.amr`), so a row shows *how* the
   reveal was authorised — `["pwd","otp","stepup"]` — and the two kinds of `otp` token stay
   distinguishable in the trail.

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
     `enrolment_failed_attempts`). The attempt that trips it answers like the ones before it
     (`422`); the `403 password-locked` / `403 mfa-locked` is what a call from a session that is
     still live gets while the lock holds, and it is refused without counting. The session that
     was ended answers `401` from then on.
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
   codes exactly as they were and returns `recovery_codes: []`. Two confirmations racing for one
   person cannot both mint a set: the credential row is locked first (B.8). The one exception is replacing a
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
7. **Bare session for `begin` and for `confirm`.** Both count failures (`confirm` since B.8), so
   both take a bare session, commit for themselves and bind the tenant first (reasoned entries in
   the tenancy route audit; `password/change` is a third, Part D).
8. **A pending factor is not a standing invitation to guess** (second review, m1–m3). `confirm` used
   to count nothing and a pending factor never expired, so a bearer that shared the beginning
   session — the only thing that may confirm it — could try codes against a seed only the real
   member had seen, and confirming a *first* factor hands over ten recovery codes. Now:
   - **The credential row is locked first** (`SELECT … FOR UPDATE`), so two confirmations for one
     person are serialised: both can no longer read "no other working factor" and each mint (the
     second replacing) the recovery codes. Tested with a real two-connection race.
   - **A pending factor expires ten minutes after it was begun** (`MFA_PENDING_FACTOR_TTL`).
   - **Wrong codes are counted on the factor** (`mfa_factors.confirm_failed_attempts`, migration
     `9c3e7a1d5b28`) and the factor is voided at the fifth (`MFA_CONFIRM_MAX_ATTEMPTS`). Counted on
     the factor rather than the credential so that mistyping while setting an app up cannot lock
     anyone out of signing in. The fifth is still answered `422 mfa-code-invalid` (its message says
     the setup was cancelled); the next call is the 404.
   - **A working factor confirmed after the pending one was begun voids it** (`superseded`).
     `begin` asks for a code from an existing factor, but a factor begun while the account had none
     was asked for only the password, and must not become a way round that question once one
     exists.
   - **`begin` replaces every pending factor the member has**, so pending rows do not accumulate and
     a stale one can never be confirmed.
   Expired, superseded and out-of-attempts confirmations are the same `404 No such enrolment`, each
   voids the factor, and each is audited (`auth.mfa_enrolment_failed` with `reason` `expired`,
   `superseded`, `invalid_code` or `attempts_exhausted`; never the code).

### C. `switch-org` to the organization you are already in is refused

`POST /auth/switch-org` is the one route that exchanges a bearer access token for a refresh token
(a cookie), so it is the cheapest way for anyone holding an access token to outlive it. Switching to
the organization the presenting token is already for changes nothing and has no legitimate caller
(the dashboard's switcher offers only the other organizations and shows "Current" for this one), so
it is `409 already-in-organization`, checked after the federated-session re-validation so a dead
SSO session is still ended rather than merely refused. See risk 8 for what this does not close.

### D. `POST /auth/password/change` is guarded like sign-in

The password is the trust anchor for adding an authenticator, and this route verified it with no
counter, no lock check and no audit — and compared `current` with `new` only *after* the password
checked out, so `current=new=candidate` answered 401 for a wrong guess and 422 for the right one:
a free oracle for a stolen bearer, at the gateway's 300 requests a minute (about 430,000 guesses a
day). Now, in `services.change_password` (bare session, tenant bound first, like `step_up`):

1. **Nothing that depends on the answer is decided first.** `current == new` and the strength rules
   for the new password are checked *before* the current password is looked at, so they say the
   same thing whether or not the guess is right, and neither is counted.
2. **The lockout is checked before the password, and a wrong password is counted** against the same
   `failed_attempts` / `locked_until` as sign-in (`_register_password_failure`) — one allowance
   across the login form, enrolment and this form. The attempt that trips the lock is itself a
   `422` like those before it, and ends the session the guesses came from
   (`password_change_failed_attempts`). **The count restarts when the lock is set**
   (`_register_password_failure`, as the second-factor counter always did), so the lock expiring is
   a real unlock: with the count left at the limit, the first wrong password after
   `lockout_minutes` locked the account again and — here and in enrol-start, where the tripping
   attempt also ends the session — one typo by the real member ended their session (second review,
   m1). `current_password` carries no length policy, so a short candidate is counted rather than
   refused for free.
3. **A wrong password is `422 password-incorrect`, field `current_password`** — never 401, which the
   shared client answers by refreshing, re-sending and signing the member out.
4. **A failure is audited** (`auth.password_change_failed`: session, reason, whether it locked;
   never either password) and committed before it is raised. **The success is audited against the
   same organization** (`auth.password_changed`), so one read of the member's organization finds the
   guesses and the outcome together; the outbox event for other services stays on the nil
   organization, because a credential belongs to a person and not to one of their organizations.
5. **The session must be a live one of this person**, so a revoked session's leftover access token
   can no longer change (or guess at) the password. An operator token is refused.

### Error contract (RFC 7807 `type` is `https://errors.opsgrids.dev/<slug>`)

| Status | Slug | Where | Meaning |
|---|---|---|---|
| 401 | `unauthorized` | all | No/invalid/expired bearer, the session is revoked, expired or not yours, or the membership has ended. Nothing counted. |
| 403 | `mfa-unavailable-for-sso` | enrol, confirm, step-up | The session came from an identity provider. Needs a password sign-in. |
| 403 | `password-locked` | enrol, password/change | Sign-in's password lockout, for a call from a session that is still live. The attempt that trips it is a 422; it ends that session, which then answers 401. |
| 403 | `mfa-locked` | enrol, step-up | Too many wrong codes; same message as the login lockout. Like `password-locked`, what a live session gets while the lock holds; the tripping attempt is a 422. |
| 403 | `mfa-reauthentication-required` | step-up | The authenticator was added after this session began and not through it. Sign in again. |
| 404 | `not-found` | confirm | No such enrolment: missing, not yours, begun by a different session, older than ten minutes, overtaken by another authenticator being confirmed, or out of attempts. |
| 409 | `mfa-not-enrolled` | step-up | No confirmed factor. Send the member to the Security page. |
| 409 | `already-in-organization` | switch-org | The token is already for that organization. |
| 422 | `password-incorrect` | enrol, password/change | Wrong password. Counted. `errors[0].field` is `password` (`current_password` at password/change). |
| 422 | `mfa-current-code-required` | enrol | The account has an authenticator: send `current_code`. Not counted. `errors[0].field` is `current_code`. |
| 422 | `mfa-code-invalid` | enrol, confirm, step-up | Wrong code. Counted at enrol and step-up against the credential; at confirm against the pending factor, which is voided at the fifth. `errors[0].field` is `code` (`current_code` at enrol). The fifth wrong code at step-up or enrol answers this too, not `mfa-locked`: the session has ended, so the *next* call is the 401. |
| 422 | `mfa-code-replayed` | enrol, step-up | A valid code for a step already spent (typically the one that just signed them in). Counted. Says to wait for the next code. |
| 422 | `validation` | all | Malformed body, unknown field, or (step-up) not six digits, including a recovery code; at password/change a new password that is too weak (field `new_password`). Not counted. |
| 422 | `business-rule` | password/change | The new password equals the current one (field `new_password`). Decided before the current password is looked at; not counted. |
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
   `mfa_failed_attempts` / `mfa_locked_until`; enrolment, password change and sign-in share one
   `failed_attempts` / `locked_until`. Someone holding a stolen bearer token can therefore lock the
   real member out of **login** for `lockout_minutes` (default 15) by guessing wrongly, and the
   attempt that trips a lock ends the session they are guessing from. This is the same exposure the
   login form already has and there is deliberately no admin unlock; expiry is the unlock.
4. **The token's scope is every reveal route, not one record.** It carries `otp` and `stepup`, and
   every reveal route across CRM (which requires both), HRMS, the storefront and Engage (which
   require `otp` alone) accepts it until it
   lapses. The holder still needs the permission, the scope and (for record-level reveals) the
   object-level check, and every reveal writes its own `entity.sensitive_read` audit row — but one
   code opens all of them for the window, not just the one the member had in mind.
5. **`otp` alone is not freshness — fixed for the CRM routes, still true elsewhere.** The first
   version of this ADR called a session that already carries `otp` an edge case. It is not:
   confirming an authenticator turns MFA on, so *every* session opened after enrolment gives a code
   at sign-in and carries `otp` for up to seven days, and a stolen session token passed a guard
   that asked only for `otp` (second review, finding 1; A.8). **The lead and customer reveal routes
   now require the step-up marker as well** and record the token's `amr` in the audit row, so a
   code was proved for the reveal, within `step_up_token_ttl_seconds`. **The other three unmask
   routes still require `otp` alone** — employee (`hrms.people.employee.read_sensitive`),
   storefront (`storefront.sales.enquiry.unmask`) and Engage (`engage.inbox.message.unmask`) —
   because their UIs do not run the step-up, so requiring the marker there would break them; for
   those, a session that gave a code at sign-in reveals with no further code, as before. **What
   that means in practice: enrolling an HR administrator, a storefront member or an Engage agent
   means their ordinary session token can unmask on those three routes** — an authenticator turns
   MFA on for every later sign-in, so every session they open carries `otp` — **until a follow-up
   wires `useStepUp` into those UIs and adds the marker to the three guards.** (Before they enrol
   they cannot unmask at all: no token of theirs carries `otp`.) Enrolment there adds a code at
   *sign-in*, not one per reveal, and should not be described as the latter. Each of the three routes
   has a test named for exactly this (`..._still_passes_this_route_risk_5`) which fails, and is to
   be rewritten as a refusal, when the gap is closed. Extending the marker to them is a one-line
   change per route (the guard, plus the UI using `useStepUp`) and is the follow-up. **Phase 2 remains:** stamp `otp_at` into the token (and out of `Session.amr`)
   and have `require(..., amr=..., max_age=...)` refuse an old one — a `platform-core` change that
   redeploys every service and needs its own release.
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
9. **The password is the trust anchor for adding a factor, and its door is now guarded** (second
   review, finding 2; Part D). `password/change` used to verify the current password with no
   counter and answer `current=new=candidate` differently for a right and a wrong guess. It is now
   counted, locked and audited like sign-in. What is left of it: the counters are shared, so a
   bearer holder can lock the real member out of signing in by guessing (risk 3). The count now
   restarts when the lock is set (Part D.2), so a lock expiring is a real unlock; before, **one**
   wrong password after expiry locked the account again — for the login form as well — and in
   password change and enrol-start that also ended the session it came from.
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
12. **Deploy window: bearer-only enrolment during the roll.** Between the migration and the old
    auth container being replaced, the *old* auth still serves `enrol` and `confirm` with a bearer
    token alone (no password, no session binding). A factor created then carries a `NULL`
    `enrolled_session_id` and is judged on its timestamp alone (`_factor_usable_in_session`), so a
    stolen bearer used inside that window could still enrol. Keep the window short — deploy auth
    immediately after the migration — and audit `auth.mfa_factors` rows created in it (see the
    checklist). The new release's own factor rules cannot see what the old release did.
13. **After `:convert`, the lead's email is plaintext in `contacts.email`.** Lead `:convert` opens
    the sealed lead email (`lead/services.py`, the conversion claim) and sends it to
    customer-service, which stores it as plaintext `contacts.email` (a work email is the contact's
    natural key, `uq_contacts_org_email`) and returns it unmasked to every CRM seat holding
    `crm.book.contact.read:ORG`. So the step-up protects a lead's phone, and the lead's email
    **only until the lead is converted**. Accepted and pre-existing: it is how a converted lead
    becomes a contact. Follow-up: seal or mask the contact email like the phone, which changes the
    contact's natural key and the CRM UI, and is its own slice.
14. **Pre-existing, listed and not changed here:** lead create answers `409 A lead with that email
    already exists` (an existence oracle for an email among an organization's leads, to a caller
    holding `crm.lead.lead.write`).
15. **`amr` reaches the services as an unsigned header.** The gateway verifies the access token and
    forwards `X-Amr` (and `X-Perms`, `X-Roles`, …) beside a signed hop token, but that token vouches
    only for the user and the organization (`uid`, `org`; `_principal_from_gateway`). So the marker
    is exactly as trustworthy as the hop: anything that can mint a forwarding token — the gateway,
    or any process holding the shared internal secret — can assert `otp` and `stepup` for any user
    it names, and a service reachable without the gateway would accept the header from anyone.
    True of every claim the services take from those headers, not only this one, and pre-existing;
    it is why the internal secret and the network boundary matter as much as they do. **Follow-up,
    out of scope:** carry `amr` (and `perms`) in the forwarding token's signed claims, so a header
    that disagrees with what the gateway saw is refused, the way `X-User-Id` and `X-Org-Id` already
    are.

## Who can reveal, really

The step-up decides whether a token *may* reveal; the grants decide who *holds* the permission.
Read together, so nobody assumes the second is narrower than it is:

Read from the role catalogues (`services/*/app/catalogue.py`) and the route guards:

| Permission | Route guard | Scope floor | Roles that hold it |
|---|---|---|---|
| `crm.book.contact.read_sensitive` | `require(..., amr={"otp", "stepup"})` | none | `CRM_BOOK_ADMIN` at `ORG` (built from `.all("ORG")`); `CRM_SALES_REP` (the default role) at `OWN`, added by the reveal release. |
| `crm.lead.lead.read_sensitive` | same | none | `CRM_LEADS_ADMIN` at `ORG` (built from `.all("ORG")`); `CRM_LEAD_REP` (the default role) at `OWN`. New in the reveal release, together with the permission itself. |
| `hrms.people.employee.read_sensitive` | `require(..., amr={"otp"})` | none | `HR_ADMIN` at `ORG`; `EMPLOYEE` at `OWN` (their own record) |
| `storefront.sales.enquiry.unmask` | `require(..., amr={"otp"})` | `ORG` | `STOREFRONT_ADMIN` only; `STOREFRONT_STAFF` holds no enquiry permission |
| `engage.inbox.message.unmask` | `require(..., amr={"otp"})` | `ORG` | `ENGAGE_ADMIN` only; `ENGAGE_INBOX_AGENT` is explicitly without it |

- **`CRM_LEADS_ADMIN` is built from `CRM_LEADS.all("ORG")`, so it now includes
  `crm.lead.lead.read_sensitive:ORG`.** Every CRM owner and administrator can therefore reveal
  **any** lead's email and phone in the organization. That is intended — it is the same as
  `CRM_BOOK_ADMIN` for contacts — but it means the step-up is the thing between an admin account
  and a lead's *phone and email*, and the audit row is the trace. **Not a barrier to the email
  once the lead is converted:** `:convert` stores it as plaintext `contacts.email`, readable by
  every seat with `crm.book.contact.read:ORG` (risk 13, accepted and pre-existing).
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
  reveal is the scope floor on the route, the grant, the `otp` (and, on the CRM routes, `stepup`)
  requirement and the audit row.

## Revisit when

- `otp_at` and a `max_age` on `require()` ship (Phase 2, risk 5): the TTL setting then becomes the
  ceiling on a token, and a per-route maximum age becomes the real control.
- An organization gains the ability to enforce MFA for its members: the login step then carries
  `otp` for every session and this endpoint becomes the exception path.
- Federated members need to reveal (risk 6).
- Revocation of individual access tokens is built (the gateway learns to check the session): risks
  1 and 2 go away.
- `switch-org` is moved onto the refresh cookie (risk 8).
- The HRMS, storefront and Engage unmask UIs run the step-up: then their routes should require the
  marker too (risk 5).
- The contact email is sealed or masked (risk 13).
- A notification rule for `core.user.mfa_enrolled` is added, so a member is told when an
  authenticator is added to their account.

## Deploy checklist

The reveal release: member step-up (auth), lead and contact reveal (lead, customer), the dashboard
Security page and the CRM Reveal buttons. **Not deployed; the owner approves the backend deploy
separately.** In order:

1. **Migrations, two, both auth, both additive.** `5d2f8a1c7e94`
   (`auth.mfa_factors.enrolled_session_id`, nullable UUID, no foreign key; down revision
   `f8e432a62a84`) and `9c3e7a1d5b28` (`auth.mfa_factors.confirm_failed_attempts`, `integer NOT NULL
   DEFAULT 0` — a constant default, so no table rewrite; down revision `5d2f8a1c7e94`, the single
   head). `make deploy` migrates before it recreates containers (preflight, build, caddy validate,
   **migrate**, recreate, health), so both columns are there before the new auth serves; being
   additive and unused by the old release, the old auth keeps working against them meanwhile. No
   manual step. Both set `lock_timeout = 5s`: if a long transaction holds `auth.mfa_factors`, the
   migration fails after five seconds (rolled back, nothing half-applied) instead of queueing every
   sign-in behind its `ALTER TABLE`; run it again once the long transaction is gone. lead and customer have no migration. `remote.sh migrate` also ends with the
   existing MFA reseal (`lib/mfa-reseal.sh`), a no-op unless a legacy plaintext factor is left;
   until such a factor is resealed, enrol and step-up answer 503 `mfa-reseal-pending`.
   **Deploy auth immediately after the migration, and audit the window** (risk 12). Between the
   migration and the old auth container being replaced, the old release still serves `enrol` and
   `confirm` to a bearer token alone; a factor made then has `enrolled_session_id IS NULL` and is
   judged on its timestamp alone. After the deploy, read what was created in that window (from the
   time `migrate` finished to the time the new `auth` went healthy) and ask each owner whether
   they enrolled it; delete any they did not:

   ```sql
   select id, user_id, created_at, confirmed_at
   from auth.mfa_factors
   where enrolled_session_id is null
     and created_at between '<migrate finished>' and '<auth healthy>';
   ```

2. **Environment.** Nothing new is required and `preflight.sh` has nothing new to check. Optional,
   defaults correct for production and not plumbed through compose: `STEP_UP_TOKEN_TTL_SECONDS`
   (auth, 180; startup fails outside 30-600) and `REVEAL_BURST_LIMIT` / `REVEAL_BURST_WINDOW_SECONDS`
   / `REVEAL_DAILY_LIMIT` (lead and customer, 30 / 600 / 200; the two share Redis keys, so keep them
   equal if ever set). `REDIS_URL` already reaches `lead` and `customer` through the shared
   `x-service-env` block and both wait for redis to be healthy; `tests/infra/test_compose_reveal_redis.py`
   pins it.
3. **Redis becomes a dependency of two more services.** lead and customer open a client and spend a
   per-user reveal budget when a value is about to be opened: after the record has been found, shown
   to be in scope (and, for a lead, to have a value on file), and before it is decrypted — so a
   request that was going to be refused spends nothing and a stolen token cannot burn the real
   person's budget. The spend is **one atomic step on the server**: a small script (`EVAL`) that
   takes both counters (burst and daily) or neither, sets an expiry on any counter that has none,
   and does not spend when it refuses — so a request refused for the burst does not also burn the
   day. It is **one round trip under one two-second timeout** (`_REVEAL_BUDGET_TIMEOUT_SECONDS`), so
   the most a request can hold a pooled database connection for while waiting on Redis is two
   seconds in total, not per window, and a timeout or a dropped connection cannot land between an
   increment and its expiry (the server runs a script it has received to the end, and does not run
   one it has not) — which the previous two-step version could, leaving a counter that never
   expired. A Redis that neither answers nor refuses is the same outage as one that is down.
   Redis down: the reveal routes answer 503 (fail closed), `/health/ready` on those two reports
   `degraded` (still HTTP 200, no restart), and nothing else in either service changes. The
   script's behaviour on a real server is pinned by `test_reveal_budget_redis.py` in each service
   (it needs a Redis at `REDIS_URL` and skips, saying so, without one), and
   `tests/infra/test_reveal_budget_parity.py` keeps the two services' copies identical.
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
7. **`POST /auth/password/change` is now counted, locked and audited** (Part D; it was the weakest
   link of the first version). The dashboard's password form maps its 422 field errors, and the
   previous dashboard already read `current_password`, so the two releases interoperate. Do not read
   step-up as protection against someone who holds both a stolen bearer token *and the password*:
   that pair still enrols an authenticator, by design, and is exactly as strong as the password.
8. **One oddity to know, not to fix now.** `enrol/confirm` on a factor that is already confirmed
   answers 401, which the shared client reads as a dead session (refresh, retry, sign out). The
   dashboard cannot send it twice, but a lost response followed by a manual retry would. A 409
   would be kinder.
9. **The three unmask routes outside CRM still take `otp` alone** (risk 5): employee, storefront and
   Engage. Deploying this release does not change them. **Enrolling HR administrators, storefront
   members and Engage agents means their ordinary session tokens can unmask on those routes** — their
   sessions carry `otp` from sign-in, for up to seven days — **until a follow-up wires `useStepUp`
   into those UIs and adds the step-up marker to the three guards.** Enrolment there is a code at
   sign-in, not a code per reveal. Only the lead and customer routes require the step-up marker; do
   not tell anyone the whole platform now demands a fresh code. Each of the three routes has a test
   (`..._still_passes_this_route_risk_5`) that fails when the gap is closed.
10. **A converted lead's email is plaintext in the contact** (risk 13), so the step-up protects a
    lead's email only until it is converted. Pre-existing, accepted, and a follow-up.
