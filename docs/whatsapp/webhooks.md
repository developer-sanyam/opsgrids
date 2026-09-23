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
receipts beside it — Meta batches them and will not send the others again.

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
   any Meta page consulted, so they are stored whole in `raw` and never parsed on a guess. The
   sender (`from`, bare digits) is canonicalised to E.164 by `webhooks.sender_e164`, which refuses
   rather than guesses a country code — the same rule `domain.normalize_recipient` applies to
   every outbound address.
2. `services.fold_inbound_message` upserts the conversation for that number (`ON CONFLICT DO
   UPDATE`, sealed address, blind-indexed for the lookup), inserts the message (sealed body,
   masked preview), and — if the text is a STOP keyword (`webhooks.STOP_KEYWORDS`) — writes a
   `Preference` refusal for every WhatsApp category, the same row a human operator's suppression
   write would produce.
3. An outbox event, `notification.whatsapp.inbound_received`, is staged in the same transaction.

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

- **Media and interactive replies.** Stored in `raw`, uninterpreted — see above.
- **Sending a template from the inbox.** The reply endpoint only ever accepts free text; there is
  no second endpoint that resolves a template send against a conversation's sealed number, so the
  inbox's composer shows what is approved and ready without a way to send it from there yet.
- **`messages.to_address` is still plaintext.** It should be sealed like every other PII column at
  rest; doing so touches the dispatcher, the campaign driver and the receipt fold across both
  channels, not just WhatsApp, and is deferred rather than half-migrated under this slice.
- **A retention policy for inbound bodies.** `inbound_messages.body_enc` has no purge job yet.

## Verified against

Read on 2026-09-19, not recalled:

- [Webhooks getting started](https://developers.facebook.com/docs/graph-api/webhooks/getting-started) — `hub.mode`/`hub.challenge`/`hub.verify_token`, `X-Hub-Signature-256`, HMAC-SHA256 over the raw payload keyed by App Secret
- [Cloud API webhook payload examples](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples) — the `entry`/`changes`/`value` envelope, the `statuses` entry, `metadata.phone_number_id`
