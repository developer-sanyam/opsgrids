# WhatsApp webhooks

What Meta sends us, and how it is trusted. Delivery and read receipts, and inbound customer
messages, both work — this document covers both halves. See [audit.md](audit.md) for what
inbound messages unlocked (conversations, the service window, STOP) and what is still open.

## The endpoint

```
GET  /webhooks/whatsapp   Meta's subscription handshake
POST /webhooks/whatsapp   message status receipts
```

Mounted at the root, outside `/api/v1`, and excluded from the OpenAPI document — this is the
provider's endpoint, not public API. It is listed in the gateway's public paths because **Meta
cannot hold one of our tokens**, so it is the only unauthenticated surface notification exposes.

## How it is trusted, since there is no token

The signature is the whole trust boundary:

```
X-Hub-Signature-256: sha256=<hex>
      = HMAC-SHA256(raw request body, App Secret)
```

Four rules, each with the failure it prevents:

| Rule | Failure prevented |
|---|---|
| The **raw bytes** are read before anything parses them | Parsing to JSON and re-serialising changes whitespace and key order, so the digest never matches and *every genuine webhook* is rejected |
| Comparison is `hmac.compare_digest`, never `==` | A byte-at-a-time compare leaks through timing how much of a forged signature was right — enough to construct one |
| Unsigned or wrongly-signed → **401, and nothing else happens** | A forged delivery receipt is a lie about a customer; a forged inbound message would be words attributed to one |
| Anything genuine gets **200**, including what we cannot use | Meta retries whatever does not return 200, so failing on an unfamiliar payload is a retry loop, not robustness |

There is a test asserting the re-serialisation trap directly: a semantically identical body with
different whitespace does not verify.

## Which tenant a webhook belongs to

Meta posts every organization's webhooks to **one URL**. The only thing in the payload that says
whose they are is `metadata.phone_number_id`.

So `provider_accounts.phone_number_id` is unique **globally**, not per organization — it is the
routing key, and two tenants claiming one number would make an inbound message unattributable.

That lookup happens *before* a tenant is known, which row-level security cannot express. It
therefore runs on notification's own SELECT-only BYPASSRLS directory reader
(`opsgrids_notification_directory`), granted `SELECT` on that one table and nothing else — the
same pattern account-service uses to resolve a person at sign-in. Everything after it runs
tenant-bound.

**Resolved per `change`, never once for the whole body.** Meta batches several WABAs' changes
into one `POST` — one platform App Secret signs all of them — so a payload can legitimately name
two, or ten, different organizations' numbers in the same request. An earlier version of this
route read `metadata.phone_number_id` once from the first change it found and used that single
organization for everything in the body; a second organization's status or message batched
alongside the first would have folded against the wrong tenant. `webhooks.group_statuses_by_phone`
and `group_inbound_by_phone` group every item by **its own change's** number first; the route then
resolves one organization per distinct number and folds that number's items inside it. A change
naming no number at all is dropped, not misrouted to whichever number happened to resolve first.

## Statuses

Meta's four, mapped onto the message lifecycle:

```
pending → sending → sent → delivered → read
                      ↘ failed        (outside the ladder)
```

**Receipts arrive out of order.** Meta batches them and the network reorders them, so a
`delivered` can follow a `read`. `domain.advances` compares rank, so a late receipt never walks
the state backwards — without it a message would report as less delivered than it is, and the
inbox would lie.

Three consequences worth knowing:

- **`failed` always applies**, because Meta reports a message as sent and then failed, and the
  failure is worth recording whatever came before it.
- **`failed` and `suppressed` are terminal.** A later `delivered` on a suppressed message means
  two messages have been confused, not that the suppressed one arrived.
- **A `read` backfills `delivered_at`.** Meta does not always send both, and a message shown as
  read but never delivered is a gap an operator would have to explain.

Meta's own error code and title are stored on a failure. "Failed" is not something an operator
can act on; `131026 Message undeliverable` is.

## Redelivery

Meta redelivers, and one message legitimately produces several statuses. So the identity of an
event is the **pair** — message id *and* status — not the message:

```
uuid5(namespace, "wamid.ABC:delivered")
```

Derived rather than stored, which lets the existing `processed_events` ledger do this job instead
of a second table. Two ledgers eventually disagree.

An event is marked processed **even when the fold applied nothing**. It has been handled:
re-running it would reach the same conclusion, and leaving it unmarked would make every
redelivery repeat the work.

One transaction per status rather than per body, so one unknown message id cannot roll back the
receipts beside it — Meta batches them and will not send the others again. Each item's fold also
runs inside its own `try`/`except`: a status or message that raises while folding is parked in
`inbound_dead_letters` (organization-scoped, RLS'd, no PII and no re-playable payload — just
`kind`, `provider_message_id`, a truncated exception message, and when it happened) rather than
failing the whole request. Without that, one malformed or unusually large item would 500 the
entire `POST`, and Meta's retry of the *whole* body would repeat the same failure forever while
never delivering the items that would otherwise have folded cleanly.

## Configuration

| Setting | Scope | Why |
|---|---|---|
| `whatsapp_app_secret` | platform | The Meta App is the vendor's; tenants onboard their WABA to it, so one secret signs every tenant's webhooks |
| `whatsapp_verify_token` | platform | Same reason — the handshake is per app, not per tenant |
| `provider_accounts.access_token_enc` | **per tenant** | A tenant's own token, sealed with `platform_core.pii.seal`. A token that can send as the customer's business is not less sensitive than a phone number |

A service with no app secret configured **refuses** webhooks rather than accepting unverified
bodies. A deployment that trusted everything would be worse than one that trusts nothing.

## Inbound messages

`POST /webhooks/whatsapp` folds `messages` alongside `statuses` in the same body, item by item,
each in its own transaction — the same shape as a status receipt, and for the same reasons
(§Redelivery above). For each one:

1. `webhooks.parse_inbound` reads the envelope. Only `type: "text"` is interpreted — the inner
   shapes of media and an interactive reply (`button_reply`, `list_reply`) are not published on
   any Meta page consulted, so they are kept whole and uninterpreted rather than parsed on a
   guess. The sender (`from`, bare digits) is canonicalised to E.164 by `webhooks.sender_e164`,
   which refuses rather than guesses a country code — the same rule `domain.normalize_recipient`
   applies to every outbound address.
2. `services.fold_inbound_message` upserts the conversation for that number (`ON CONFLICT DO
   UPDATE`, sealed address, blind-indexed for the lookup), inserts the message, and — if the text
   is a STOP keyword (`webhooks.STOP_KEYWORDS`) — writes a `Preference` refusal for every WhatsApp
   category, the same row a human operator's suppression write would produce; a START keyword
   (`webhooks.START_KEYWORDS` — `START`, `UNSTOP`, `SUBSCRIBE`) lifts it the same way. Both match
   is exact, trailing punctuation stripped (`"STOP."`, `"Stop!"`), never a substring.
3. An outbox event, `notification.whatsapp.inbound_received`, is staged in the same transaction.

**Nothing here is plaintext PII at rest.** `inbound_messages.raw_enc` seals the provider's whole
message object — including `from` and the text body — and there is no plaintext `raw` column at
all: an allowlist of "fields to strip" would only be as safe as the last person to update it when
Meta adds a new message shape, so the whole object is sealed instead. The sender's profile name is
sealed the same way (`profile_name_enc`). `body_masked` and `profile_name_masked` are **fixed-shape
placeholders** — a constant run of bullets, and a first-initial-plus-bullets respectively — never a
function of the real value's length: a length- or tail-revealing mask on free-form text leaks the
one thing masking exists to hide, in a way a masked phone number does not, and a length-dependent
mask on top of a fixed-width column is also what let a long enough customer message crash the fold
outright. Opening either sealed column is `engage.inbox.message.unmask`, the audited, stepped-up
permission (`services.unmask_inbound_message`).

Idempotency is the message id alone (`webhooks.InboundMessage.dedupe_id`), unlike a status's
message-and-status pair — one customer message has one identity. `inbound_messages.
provider_message_id` is additionally unique per organization, so a bug in that derivation would
surface as a database error rather than a duplicate message in an inbox.

**Loop protection (C14).** `notification.whatsapp.inbound_received` is deliberately **not** in
`domain.SUBSCRIBABLE_EVENTS` — the list a `Rule` may name and the generic consumer fans an event
through `apply_rules`. An inbound message auto-triggering an outbound one on the same conversation
is exactly the loop that guard exists to prevent, so opting an organization's automation into
reacting to a customer's own message is a deliberate code change (adding the routing key to that
list), never a `Rule` row an operator can create unsupervised.

## Contact resolution

A separate consumer, `consumers.fold_inbound_contact`, binds the same event and asks
customer-service which contact holds the sender's number (`POST .../contacts:resolve`, the keyed
blind-index lookup — see that endpoint's docstring). It is a second idempotent fold over the same
event id, keyed by a derived id (`uuid5(event_id, "contact_resolve")`) so it does not collide with
whichever other consumer also folds the raw event id.

`app/contacts.py` **degrades to an unknown sender on anything short of a clean match** — no CRM
entitlement, unreachable, a timeout, an unexpected status — rather than raising. That is the
opposite of `app/targets.py`'s outbound resolver, which raises so the broker retries: there, a
customer is owed a notification and silence would mean they never hear it. Here, the conversation
and its message are already durably committed by the time this runs; a CRM that cannot answer
leaves `contact_id` unset, and a later redelivery or manual retry resolves it once it can.

## The 24-hour service window and STOP

`webhooks.within_service_window(conversation.last_customer_message_at)` is the whole of Meta's
rule: free text is possible only inside 24 hours of the customer's last message. It is evaluated
server-side wherever it matters — read time (`ConversationOut.within_service_window`, so the inbox
never reimplements it) and write time (`services.reply_to_conversation`, which row-locks the
conversation for the check and refuses with `409` and `remedy: "template"` outside it, before
anything is staged).

STOP (`STOP`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT` — exact match, not a substring, so a sentence
using the word is not an opt-out) writes the same `Preference` suppression a human operator's write
would. `services.stage_message` checks it at render **and** at dispatch, so a reply already typed
when a customer says STOP is staged as `suppressed` rather than sent — "we deliberately did not
tell this customer" is a fact kept on the row, the same as any other suppressed send.

## Not built yet

- **Media and interactive replies.** Kept whole in `raw_enc`, uninterpreted — see above.
- **Sending a template from the inbox.** The reply endpoint only ever accepts free text; there is
  no second endpoint that resolves a template send against a conversation's sealed number, so the
  inbox's composer shows what is approved and ready without a way to send it from there yet.
- **`messages.to_address` is still plaintext.** It should be sealed like every other PII column at
  rest; doing so touches the dispatcher, the campaign driver and the receipt fold across both
  channels, not just WhatsApp, and is deferred rather than half-migrated under this slice.
- **`preferences.recipient` is still plaintext.** A phone number or email address, unsealed, in
  the one table that decides whether anything may be sent to it — noted here rather than fixed in
  this pass, which was already touching the STOP/START write path and did not need to touch this
  column's shape to do it.
- **A retention policy for inbound bodies.** `inbound_messages.body_enc`/`raw_enc` have no purge
  job yet.
- **`inbound_dead_letters` has no operator surface.** The rows exist and RLS protects them; nothing
  reads them yet but a direct query.

## Proving a connection before trusting it

`POST`/`PUT /engage/whatsapp/connection` used to record whatever WABA id, phone number id and
token a caller supplied — including a number the caller does not control, since nothing checked.
`app/whatsapp_provider.py`'s `verify_ownership` now calls Meta's Graph API with the supplied token
before either write: `GET /{phone_number_id}` (does this token see this number at all) and
`GET /{waba_id}/phone_numbers` (is the number actually listed under the named WABA). Both have to
answer yes; a token that can read the number but the number is not listed under that WABA is
exactly as unproven as one that cannot read it at all. Neither call goes through
`platform_core.egress`'s SSRF guard — the Graph API base is platform configuration
(`Settings.whatsapp_api_base`), never a URL a caller supplies, so there is no admin-chosen target
to vet.

The global-uniqueness check (§Which tenant a webhook belongs to) also moved: it used to run on the
tenant-scoped session, which row-level security filters to the caller's own organization — so it
could never actually see another tenant's row to conflict with, and a genuine duplicate surfaced
as a raw `IntegrityError` (a 500) rather than the `409` the code claimed to return. It now runs on
the same cross-tenant directory reader the webhook route uses, with the database's unique
constraint kept as the second, race-proof net around the insert itself.

## Assigning a conversation

`POST /engage/conversations/{id}:assign` used to write whatever `assignee_membership_id` a caller
supplied with no check at all — an id from a different organization, or one naming nobody, was
recorded exactly as though it were real. `app/memberships.py`'s `membership_is_active` asks
account-service's `GET /internal/v1/organizations/{org}/memberships/{id}` first, and the write is
refused with `422` unless the answer is a confirmed, active membership in the caller's own
organization. This fails **closed** — unlike `app/contacts.py`'s degrade-to-unknown for a display
fact, an assignment target this service cannot confirm must never be treated as good, because a
wrong assignment is not a cosmetic gap the inbox shows plainly; it is a conversation nobody who was
supposed to answer it will ever see. Clearing an assignment (`null`) needs no such check — there is
no id to misroute to.

## Verified against

Read on 2026-09-19, not recalled:

- [Webhooks getting started](https://developers.facebook.com/docs/graph-api/webhooks/getting-started) — `hub.mode`/`hub.challenge`/`hub.verify_token`, `X-Hub-Signature-256`, HMAC-SHA256 over the raw payload keyed by App Secret
- [Cloud API webhook payload examples](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples) — the `entry`/`changes`/`value` envelope, the `statuses` entry, `metadata.phone_number_id`
