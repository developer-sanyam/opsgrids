# ADR-0023 — Member step-up: prove a one-time code, get a short-lived `otp` token

Status: **accepted (built)** · Date: 2026-09-27 · Service: `auth`

Builds on [ADR-0000](0000-phase-0-decisions.md) §2 (only `auth-service` mints tokens), §3 (an access
token carries no permission list) and the 2026-09-17 note under §5 (`is_dangerous` is metadata, not
a control); [ADR-0003](0003-surfaces-and-authorization-topology.md) (the gateways verify, the
services authorize). The guard it feeds is `platform_core.auth.require(perm, amr={"otp"})`.

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

**`POST /api/v1/auth/mfa/step-up`**, body `{code}`, bearer-authenticated. It verifies a TOTP code
for the signed-in member and returns the existing `TokenOut` (`access_token`, `token_type`,
`expires_in`, plus the organization and membership ids) — an **access token whose `amr` is the
session's `amr` plus `otp`**.

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
   like `mfa_verify`, and `services.step_up` sets the tenant GUC itself as the first statement. The
   route audit in `tests/test_tenancy.py` carries an explicit, reasoned exemption for it and a
   behavioural test that the binding happens first even when the request is refused.

### Error contract (RFC 7807 `type` is `https://errors.opsgrids.dev/<slug>`)

| Status | Slug | Meaning |
|---|---|---|
| 401 | `unauthorized` | No/invalid/expired bearer, the session is revoked or expired, or the membership has ended. |
| 403 | `mfa-unavailable-for-sso` | The session came from an identity provider. Needs a password sign-in. |
| 403 | `mfa-locked` | Too many wrong codes; same message as the login lockout. |
| 409 | `mfa-not-enrolled` | No confirmed factor. Send the member to the Security page. |
| 422 | `mfa-code-invalid` | Wrong code. Counted. |
| 422 | `mfa-code-replayed` | A valid code for a step already spent (typically the one that just signed them in). Counted. Says to wait for the next code. |
| 422 | `validation` | Not six digits (including a recovery code), unknown field. Not counted. |
| 503 | `mfa-reseal-pending` | A legacy factor awaiting `bin/reseal-mfa-factors`. Not counted. |

A wrong code is **422, not 401**, on purpose. The shared client answers a 401 on a bearer-authenticated
call by refreshing the token and re-sending the same request, then signing the user out if that
fails: a mistyped digit would be counted twice and end the session. With 401 reserved for a bearer or
session that is actually bad (nothing has been counted when it is raised), the client's default
refresh-and-retry is the right behaviour for it and the step-up call needs no special options.

## Alternatives rejected

- **Write `otp` into `Session.amr` on success.** Simple, and it is what login does — but the
  session is what refresh mints from, so one code would unlock every reveal route for up to seven
  days, on every device sharing the session. The whole point of a step-up is that it ends.
- **Also mint a refresh token so the step-up can be renewed.** A second long-lived credential whose
  only purpose is to keep a privilege alive; the reveal is a moment, not a mode.
- **Accept recovery codes.** They are finite and irreplaceable until re-enrolment.
- **A separate lock counter for step-up.** Two counters against one secret double the attacker's
  guesses, and a tidy-looking separation is not a defence.
- **Add a `require(... amr=...)` freshness check (`otp_at`) in `platform-core`.** Right in the long
  run (below) but it redeploys every service; this slice changes only `auth`.

## Risks accepted

1. **The token cannot be revoked inside its TTL.** Access tokens are verified by signature at the
   gateway; only `pv` (a permission change) and the session's refresh ending act on them. A stolen
   step-up token opens the reveal routes for up to `step_up_token_ttl_seconds` (default 3 minutes),
   for the permissions its holder already had. It cannot be renewed, and it grants nothing the
   session did not already permit apart from the `otp` claim. Keep the TTL short; the setting is
   bounded at 600 for exactly this reason.
2. **Lockout is coupled to login MFA.** Step-up shares `Credential.mfa_failed_attempts` and
   `mfa_locked_until` with `verify_mfa`, by design. Someone holding a stolen bearer token can
   therefore lock the real member out of their **login** second factor for `lockout_minutes`
   (default 15) by guessing wrongly, and the attempt that trips the lock ends the session they are
   guessing from. This is the same exposure the login step already has (with a password, rather
   than a bearer, as the entry ticket) and there is deliberately no admin unlock; expiry is the
   unlock.
3. **The token's scope is every reveal route, not one record.** It carries `otp`, and every
   `require(..., amr={"otp"})` route across CRM, HRMS, the storefront and Engage accepts it until it
   lapses. The holder still needs the permission, the scope and (for record-level reveals) the
   object-level check, and every reveal writes its own `entity.sensitive_read` audit row — but one
   code opens all of them for the window, not just the one the member had in mind.
4. **No freshness for sessions that already carry `otp`.** A session that presented a code at login
   has `otp` in `Session.amr` and so in every token it ever mints. The guard checks presence, not
   age, so for those sessions "step-up" is satisfied for the session's whole life. This slice does
   not change that. **Phase 2:** stamp `otp_at` into the token (and out of `Session.amr`), and have
   `require(..., amr=..., max_age=...)` refuse a token whose `otp_at` is older than the route
   allows. That is a `platform-core` change and redeploys every service; it needs its own release.
5. **Single sign-on members cannot reveal.** A federated session is refused (`mfa-unavailable-for-sso`)
   and an SSO-only account has no password to sign in with instead. Making that reachable needs a
   decision about whether an external issuer's own MFA claim (`amr`/`acr` in the ID token) may
   satisfy `otp`, which is a policy question this ADR does not settle.
6. **Entitlements degrade to empty.** As at login and refresh, an unreachable product-service
   yields a token with no `ent`, and the entitlement check on the reveal route then refuses (402)
   while the code has already been spent for its step. The member retries with the next code.
7. **The `code` audit-redaction key is not on the shared list.** `platform_core.audit.REDACTED_KEYS`
   has no `code`. It is not needed here — the step-up never puts the code in an audit payload and a
   test pins the payload's field set — but a future caller that passes a `code` field to
   `record_audit` would not be protected. Add it the next time `platform-core` is released for
   another reason.

## Revisit when

- `otp_at` and a `max_age` on `require()` ship (Phase 2 above): the TTL setting then becomes the
  ceiling on a token, and a per-route maximum age becomes the real control.
- An organization gains the ability to enforce MFA for its members: the login step then carries
  `otp` for every session and this endpoint becomes the exception path.
- Federated members need to reveal (risk 5).
- Revocation of individual access tokens is built: risk 1 goes away.
