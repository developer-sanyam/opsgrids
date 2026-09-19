# WhatsApp architecture

What is actually built, as of the customer-targeting phase. Documents behaviour, not intent —
anything still missing is in [audit.md](audit.md).

## The outbound path

```
business event  (any of 91 routing keys, from any product)
      ↓         outbox → relay → notification.events queue
automation rule (per organization, data not code)
      ↓
recipient        one of three sources, exactly one per rule
      ↓            ├── recipient_literal      a fixed address
      ↓            ├── recipient_path         an address carried in the event
      ↓            └── recipient_contact_path a CONTACT ID → resolved below
      ↓
customer-service GET /internal/v1/organizations/{org}/contacts/{id}/messaging-target
      ↓          opens the sealed number, audits the read, returns E.164
opt-out check    notification.preferences, per channel and category
      ↓
template         a provider template (Meta-held) or a body we render
      ↓
validation       every variable present, positions contiguous, template approved
      ↓
idempotency      unique (organization_id, dedupe_key)
      ↓
message row      state `pending`, committed with the event's processed_events row
      ↓          outbox → relay → notification.dispatch queue
dispatcher       claim → send → record, three transactions, no network inside one
      ↓
Meta Cloud API   v25.0, `type: template` outside the 24-hour window
```

## Where the decryption boundary sits, and why

A customer's phone number is sealed in the CRM (`platform_core.pii`). Notification needs the
digits to send; it does **not** get the key or the `customer` schema.

```
notification            customer-service
------------            ----------------
knows: contact id       owns: contacts.phone_enc
                        owns: the PII key
      │
      │  GET .../messaging-target?channel=whatsapp
      │  X-Internal-Token: addressed to "customer"
      ├───────────────────────────────►
      │                        open_sealed()
      │                        audit: entity.sensitive_read
      │                        audit: contact.messaging_target_resolved (acting_as)
      ◄───────────────────────────────┤
      │  { "phone": "+91..." }
```

Three consequences worth stating:

- **The automation engine never holds a key.** It knows a contact id and a channel.
- **Every machine read is audited**, naming the calling service in `acting_as` — taken from the
  token's verified `iss`, not from anything the caller asserts about itself.
- **A later database-per-service split stays a configuration change**, because nothing reads
  another service's tables (ADR-0000 §10).

## Two failures that must not be conflated

The resolver returns `None` or raises, and the difference is the whole design:

| Outcome | Meaning | What happens |
|---|---|---|
| `None` | the contact has no sealed number | the rule skips; retrying would never help |
| `TargetUnavailable` | customer-service could not answer | the exception reaches the consumer, the message is nacked, **the broker redelivers** |

Console's price resolver tolerates its downstream being absent by returning zero, because a
missing price only understates a metric. A missing phone number means a customer is not told
something, so silence is not an acceptable degradation here.

## Channel-agnostic by construction

Nothing in the rules engine is WhatsApp-specific. `channel` is a column; `Transport` is a
protocol with three implementations; `provider_templates` is named for the pattern (register at
the provider, reference by name) rather than for Meta, because SMS in India carries
DLT-registered templates with the same lifecycle. Adding email or SMS to a rule is a row, not a
branch.

```
                    automation engine
                           │
             ┌─────────────┼─────────────┐
             ↓             ↓             ↓
          WhatsApp       Email          SMS
             │             │
     Meta Cloud API      SMTP
```

## What is not built

See [audit.md](audit.md) for the full list with sequencing. The largest gaps: inbound webhooks
(so no delivery receipts, no replies, no STOP handling), conversations and the 24-hour window,
the scheduler (so no "three days before due"), media, interactive messages, and all UI.
