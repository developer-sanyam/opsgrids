# WhatsApp webhooks

What Meta sends us, and how it is trusted. Documents what is built; delivery and read receipts
work, inbound customer messages do not yet (see [audit.md](audit.md)).

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

## Not built yet

Inbound customer messages — text, media, interactive replies — are not persisted. The envelope
and status shapes were verified against Meta's pages; the inner shapes of an interactive reply
(`button_reply`, `list_reply`) are not published on the pages consulted, so they are not parsed
rather than guessed at. Conversations, the 24-hour window and STOP handling follow from inbound
messages and are in [audit.md](audit.md).

## Verified against

Read on 2026-09-19, not recalled:

- [Webhooks getting started](https://developers.facebook.com/docs/graph-api/webhooks/getting-started) — `hub.mode`/`hub.challenge`/`hub.verify_token`, `X-Hub-Signature-256`, HMAC-SHA256 over the raw payload keyed by App Secret
- [Cloud API webhook payload examples](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples) — the `entry`/`changes`/`value` envelope, the `statuses` entry, `metadata.phone_number_id`
