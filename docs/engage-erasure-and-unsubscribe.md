# Engage: contact erasure and unsubscribe links — what is guaranteed, and what is not

Notes on two related mechanisms in `services/notification` (Engage): erasing a deleted
contact's address out of a campaign's own delivery history, and the public, unauthenticated
unsubscribe link that address history feeds into. Both touch PII outside the ordinary
authenticated read/write path, which is exactly where an unstated assumption is most likely to
be wrong later. This is where it is stated.

## Erasure: what "erased" means here

`services.erase_contact_campaign_copies` runs when a contact is deleted (`delete_contact`, the
only trigger this platform has today — see below). It overwrites `campaign_recipients.email_enc`
with a fresh seal of a fixed placeholder, clears `email_masked`/`email_index`, and — since this
review — fails any row still `queued` on the spot rather than leaving it for an ordinary claim
to pick up.

**This is an application-level overwrite, not a cryptographic shred of a per-row key.** Engage's
PII envelope (`platform_core.pii`) derives every row's encryption key from one organization-wide
passphrase (`Settings.pii_encryption_key`); there is no per-row or per-contact key to destroy.
Overwriting the ciphertext removes the *only* copy this application will ever read again, but it
does not reach:

- **Backups.** Any snapshot taken before the overwrite committed still holds the original
  ciphertext, decryptable with the same organization-wide key, for as long as this deployment's
  backup retention window says.
- **WAL.** Postgres's write-ahead log holds the pre-overwrite row until archived WAL is recycled
  per the cluster's retention setting.
- **Dead tuples.** An `UPDATE` in Postgres does not rewrite a row in place; it leaves the old
  version as a dead tuple until `VACUUM` reclaims it. Until then it is present on disk, not
  reachable through the application, but not gone either.

None of this is unique to Engage — it is true of every `UPDATE` in this schema — but it is
called out here because "erase" is a word a data-subject request takes literally, and the
honest answer for *when* the address stops existing anywhere is "no later than this
deployment's backup retention plus WAL retention plus the next autovacuum," not "the instant
the request returns 204."

## Erasure: what the two race-condition fixes below cover, and what they do not

Two windows exist between a contact being deleted and a campaign actually mailing them, and
both are closed to the extent they can be without serialising every campaign expansion behind
every contact deletion (a trade this codebase does not take — campaigns send far more often
than contacts are deleted):

1. **Expansion vs. deletion.** `expand_campaign` reads a page of `sendable_contacts` and, in a
   later statement in the *same* transaction, copies each one's sealed address into a new
   `campaign_recipients` row. Without a lock, a `delete_contact` committing in that gap would
   run its own erasure pass and find nothing to erase — the copy `expand_campaign` was about to
   write did not exist yet. `sendable_contacts(lock=True)` takes `SELECT ... FOR KEY SHARE` on
   the page it reads, which Postgres's row-lock rules make a concurrent `DELETE` on the same row
   wait for; the delete (and its erasure) can now only ever run before this transaction starts
   or after it commits, never straddling it.
2. **Claim vs. erasure.** `claim_campaign_batch` decrypts an address and builds the outgoing
   message inside a short transaction, then hands the batch to the driver, which resolves a
   transport and calls it — real network I/O, with no transaction open. A contact deleted in
   that window is caught two ways: `erase_contact_campaign_copies` immediately fails any row
   still `queued` (closing the case where erasure runs *before* the claim), and
   `drop_erased_from_batch` re-reads `erased_at` for the batch's own recipient ids immediately
   before the transport call (closing most of the case where erasure runs *after* the claim).

What neither closes: the instant between `drop_erased_from_batch`'s read and the transport
actually being invoked. Closing that would mean holding a lock across a network call to an
SMTP server, which turns a slow or hanging provider into a stuck erasure everywhere in the
organization — a worse failure mode than the sub-millisecond window it would remove. This is an
accepted, irreducible gap, not an oversight.

## Erasure: what is deliberately *not* touched

- **Suppressions.** A `Suppression` row is never erased by contact deletion, and never will be
  by design (`models.Suppression`'s own docstring): the whole point of a suppression is to
  outlive the contact that created it, so a re-imported address that once unsubscribed stays
  unreachable. Its sealed address is therefore a second place a fully "erased" contact's
  address still exists, on purpose, for as long as the suppression itself is not lifted.
- **Audit rows.** `record_audit`'s `before`/`after` payloads for `engage.contact.created`,
  `.updated` and `.deleted` carry the contact's **name** in the clear (never the address — see
  `AuditLogMixin`'s `REDACTED_KEYS`, which does not cover a contact's display name because it is
  not classified as sensitive the way a credential or a government id is). Erasing a contact
  does not retroactively scrub its name out of audit history; the audit trail is intentionally
  immutable (CLAUDE.md: "Audit is a shared mixin plus a helper... never a bespoke per-service
  log table") and a name is not currently treated as PII this platform encrypts at rest.
- **The one erasure trigger.** `grep -rn "erasure\|erase\|forget" services libs` finds no
  generic, cross-service data-subject erasure mechanism on this platform today —
  `delete_contact` is the only path that reaches `erase_contact_campaign_copies`. It is written
  to take an `organization_id`/`contact_id` rather than a `ScopeContext` specifically so a
  future generic erasure event can call it the same way `services.unsubscribe` already handles
  a suppression with no principal, but that consumer does not exist yet.

## Unsubscribe links: v1 leaked the address, v2 does not

Before this review, every unsubscribe link (`domain.make_unsubscribe_token`) encoded the
recipient's address, base64'd, directly in the URL path:
`<org>.<address>.<mac>`. Base64 is not secrecy — anyone who sees the URL sees the address —
and the URL is not seen only by the recipient. Every reverse proxy and gateway between this
platform and a recipient's mailbox logs the request path by default, so the address landed in
Caddy's and the gateway's access logs, in the clear, for as long as those logs are retained.
That is a PII leak to *infrastructure*, not to the recipient the link was written for.

**The fix, not just the documentation of it:** every link a campaign send now generates
(`services.unsubscribe_token_for_recipient` / `unsubscribe_url_for_recipient`) is a v2 token —
`v2.<org uuid>.<campaign recipient uuid>.<mac>` — which decodes to no address and no blind
index, only two ids an access log can do nothing with on their own. Resolving one
(`services.unsubscribe_recipient`) reads the sealed address straight off the named
`campaign_recipients` row; nothing about the link itself carries it.

**Old links still work, for a stated window.** `verify_unsubscribe_token` still accepts the v1
shape — mail already delivered before this change carries v1 links, and a recipient's ability
to leave must not regress the day this ships. `services._V1_UNSUBSCRIBE_SUNSET` is the date
after which a v1 link stops verifying at all (currently set roughly 90 days out from when this
note was written); after that date the only unsubscribe links this service will honour are ones
it minted itself under v2. Extending the sunset is a one-line change if 90 days turns out to be
too short for a long-lived newsletter's backlog; shortening it is the same change in the other
direction.

## Related documents

- `services/notification/app/domain.py` — the "engage unsubscribe links" module docstring is
  the implementation-level source of truth for the v1/v2 token shapes above.
- `services/notification/app/services.py` — `erase_contact_campaign_copies`,
  `drop_erased_from_batch`, and `verify_unsubscribe_link`'s docstrings cover each mechanism in
  the detail this document only summarises.
- `libs/platform-core/src/platform_core/gateway.py` — `GatewayPolicy.public_path_prefixes` is
  the separate, gateway-level fix that makes the unsubscribe link reachable without a bearer
  token at all (it was previously refused with 401 before ever reaching notification-service,
  because the gateway's public-path list could only express an exact path, never one with a
  variable segment).
