# ADR-0017 — Email-domain verification: takeover safety and the demotion policy

Status: **accepted** · Date: 2026-09-23

## Context

ADR-0000 §1 fixes the rule: email domain never silently selects an organization; it may only
select an identity provider, and only from a domain that has been DNS-verified. Two gaps in
the first implementation of that rule surfaced in review, both load-bearing enough to record
here rather than only in a commit message.

**The takeover.** `services/auth/app/services.py`'s `sso_callback` verified an ID token's
signature, issuer, audience, nonce and `email_verified` claim, but never checked that the
*email itself* belonged to the domain the flow had proved. An organization that configures
its own OIDC provider for a domain it genuinely owns can make that same, entirely honest
provider assert *any* email address — including a real user's, reached through a completely
different domain that user has nothing to do with the attacker's domain. `switch_organization`
and `rotate_refresh` had the same shape of gap one level up: a session minted by an identity
provider trusted for organization A could move to any other organization the same person
happens to belong to, on the strength of a token that proved nothing about that second
organization at all.

**The one-shot verification.** A domain was checked once, at claim time, and never again.
DNS is not immutable — a registrar migration, an expired zone, a deliberately removed record —
and nothing noticed when a verified domain quietly stopped resolving.

## Decision

### 1. An SSO identity is scoped to the domain it proved

`sso_callback` now requires `normalise_sso_domain(domain_of(identity.email)) ==
pending.domain`, and re-resolves `pending.domain` against the directory a second time,
confirming it is still verified to the same organization, before mapping the identity to a
member. A provider proving ownership of `acme.com` may only ever vouch for `@acme.com`
addresses; it has proved nothing about anyone else's domain, however real the platform account
behind that other address is.

### 2. A federated session is pinned to its organization

`Session.idp` already recorded `"oidc:<organization_id>"`. `switch_organization` and
`rotate_refresh` now read that pin (`_federated_organization_pin`) and refuse to move the
session to any other organization. A password session (`idp = "local"`) is unaffected — it
never claimed to have proved anything about a domain in the first place, so there is nothing
to pin it to. Moving to a different organization from a federated session requires a fresh
primary sign-in, which is the only place a domain gets re-proved.

### 3. Periodic re-verification, and demotion only on a sustained failure

`app/cron.py`'s sweep re-asks DNS for every verified domain on a schedule
(`Settings.domain_recheck_interval_seconds`, discovered on the existing SELECT-only directory
role — no new grant). A single miss is recorded (`last_check_ok`, `consecutive_failures`,
`failing_since` set on the transition into failing) and changes nothing else: revoking single
sign-on for an entire organization on the first transient DNS hiccup would fail far more often
than it protects anything.

**Demotion is automatic, but only past two independent bars, both required:**

- `failing_since` at least `DEMOTION_FAILING_FOR` (14 days) in the past, **and**
- `consecutive_failures` at least `DEMOTION_MIN_CONSECUTIVE_FAILURES` (3).

Both, not either — an incident that fails a check every few minutes for an hour must not
demote a domain by lunchtime on failure count alone, and a domain checked rarely that happens
to fail twice a month apart must not demote on elapsed time alone. When both hold,
`services.recheck_domain` clears `verified_at` itself, sets `demoted_at`, writes an audit row,
and publishes `core.identity.domain.demoted`. The internal SSO lookup
(`GET /internal/v1/domains/{domain}`) filters on `verified_at IS NOT NULL`, so a demoted domain
stops being usable for sign-in the instant the transaction commits — silently to the browser
(the same generic refusal an unknown domain gets), never silently to the organization: the
audit row and the event exist so an administrator finds out
(`infrastructure/edge/RUNBOOK.md`). Re-earning verification is an ordinary re-claim and
re-verify; nothing here does it automatically, the same way nothing auto-reinstates a
suspended organization.

### 4. Shape hardening that closes real gaps, not just theoretical ones

- Every trailing dot is stripped (`rstrip(".")`), not just one — a domain typed with two or
  three trailing dots used to be refused with a confusing "invalid" error instead of being
  read the way a resolver reads it.
- The claim length ceiling is 236 characters, not 253: `_opsgrids-verify.` (17 characters) is
  prepended to build the TXT record's own name, and a domain at the old 253-character ceiling
  could never actually be verified — its own verification record's name would already be
  refused by DNS's 253-octet FQDN limit.
- RFC 6761 special-use names (`local`, `internal`, `test`, `example`, `invalid`, `onion`,
  `localhost`, `home.arpa`, `arpa`) are refused outright: nothing under one of these can ever
  resolve on the public internet, so a claim on one could never be honestly verified in the
  first place.
- An organization may hold at most `MAX_DOMAINS_PER_ORGANIZATION` (50) domains at once — not a
  security boundary, but a bound on an otherwise-unbounded row count the re-check sweep would
  otherwise have to walk forever.
- `verify_domain` writes through one conditional `UPDATE ... WHERE id AND organization_id AND
  verified_at IS NULL AND verification_token = :checked RETURNING`, closing a TOCTOU window
  where a concurrent re-claim rotates the token between the DNS check and the write landing.
  No matching row maps to a 404 or a named 409; nothing here ever surfaces as an unhandled
  exception.
- `dns_check` treats `dns.exception.DNSException` — dnspython's own common base — as "no
  answer", not only the three named exceptions a prior version enumerated by hand.

## What this rejects

- **Trusting `email_verified` alone as proof the provider may speak for that address.** It
  proves the provider is willing to vouch for the address; it says nothing about which domain
  the organization actually verified ownership of.
- **Silent, unconditional auto-revocation on any DNS failure.** Registrars have bad days;
  demoting a domain on the first miss would be a self-inflicted outage far more often than a
  real security event caught.
- **A single-condition demotion trigger** (age alone, or failure count alone) — see the two
  incident shapes in §3 that a single condition would get wrong.
- **Reassigning a released or abandoned domain automatically.** Out of scope here; see
  `services/account/app/services.py`'s own "domains" section for the follow-up this leaves
  open for a *different* organization reclaiming a domain abandoned mid-claim.

## Consequences

- An SSO sign-in now costs one extra cross-tenant directory read per callback (the
  re-resolve). Cheap, and on the path that already makes at least one such read to map the
  identity to a member.
- A demoted domain requires a human to notice the event or the audit row and act — there is no
  automated recovery path, by design (§3).
- `OrgDomain` gained four columns across two migrations this slice
  (`last_checked_at`, `last_check_ok`, `failing_since`, `consecutive_failures`, `demoted_at`);
  none of them gate anything on their own except through `should_demote`.

## Revisit when

- SAML ships (docs/backlog.md): `_federated_organization_pin` already recognises a `saml:`
  prefix ahead of time, but nothing mints one yet.
- The 14-day / 3-check demotion bars turn out to be miscalibrated in practice — they are
  constants in `app/domain.py`, not schema, and moving them costs no migration.
