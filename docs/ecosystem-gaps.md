# Ecosystem gaps

What the sales material promises, what the code actually does, and what closing the difference
costs. Written because an outbound email offered nine capabilities and the honest count was four.

The platform is not thin — thirty-odd services, a real GST engine, working commerce. The gap is
narrower and more specific than "products are unfinished", and narrower than a first count
suggests. Eighty-two `publish_event` call sites stage events into outboxes and the relays drain
them to RabbitMQ. Six services consume: `account`, `console`, `finance`, `payment`, `product` and
`subscription`, twelve bindings between them.

Look at *which* twelve and the real shape appears. Every one of them is commerce or core:
`commerce.subscription.*`, `commerce.invoice.*`, `commerce.credit.*`, `commerce.payment.*`,
`core.product.activated`, `finance.compliance.invoice.filing_requested`. The billing spine is
genuinely wired end to end — an invoice finalizes, payment reacts, subscription reacts to the
payment, console folds all of it into a projection.

**This paragraph predates the consumers, and the stale-docs sweep (R35,
[decisions.md](decisions.md)) owns re-checking it for the other products. For the CRM it is no
longer true:** customer consumes `crm.pipeline.activity.logged`, and notification binds the
nine `crm.*` keys it lists as subscribable (the four lead keys joined in R1.2b); `deal` and `lead`
consume nothing ([crm.md](crm.md) §8). The original claim was that no product event was consumed by anything:
`hrms.*`, `support.*`, `itsm.*`, `operations.*`, `projects.*`, `pulseboard.*` and `marketing.*`
were published carefully, drained faithfully, and delivered to nobody.

That is what the sales list could not honestly claim. "Automation" is not a feature built once
per product — it is what a consumer does with an event somebody else published. Commerce proved
the machinery works. The products were never connected to it.

## The claims, audited

| Claimed | State before this work | Where it stands |
|---|---|---|
| WhatsApp automation | **Absent.** The string appeared once, in website copy. | Built — `notification`, WhatsApp Cloud transport |
| Email automation | **Absent.** `notification` was a 187-line scaffold whose only route returned its own name. | Built — `notification`, SMTP transport |
| Billing & inventory | Built. `finance` (11k lines, GST/GSTR-1/e-way), `billing`, `payment`, `operations`. | Unchanged |
| Website development | Agency work, outside this repo. | Out of scope here |
| Website security | Agency work. The platform's own security (RLS, MFA, rotation) is real and separate. | Out of scope here |
| CRM & lead management | **Partial.** `customer` and `deal` are real; `lead` and `pipeline` were scaffolds. | Built — §2 |
| Sales & marketing automation | **Partial.** `marketing` is real; the automation half needs consumers. | Unblocked by §1 |
| Custom software development | Agency work. | Out of scope here |
| Analytics & reporting | **Partial.** Analytics exists inside `marketing` and `projects`; there is no cross-product surface. | Planned — §3 |

## 1. Notification — the keystone (built)

Chosen first because it is the only gap that closes two claims *and* connects the products to
the machinery commerce already proved. A notification service is, structurally, the platform's
first *product-side* consumer: it reacts to events from HRMS, CRM, support, ITSM, operations and
projects without importing a line of any of them.

**The coupling is the routing key and the documented payload — never an import.** A rule binds
`support.desk.ticket.created` whether or not support is deployed on this branch; an unbound
producer simply sends nothing until someone writes a rule.

What it holds:

- `templates` — a named body per channel, rendered with `{{placeholders}}`.
- `rules` — *when this event happens, send this template on this channel to this recipient.*
  This is the automation engine, and it is per-organization data rather than code, so adding an
  automation is a row and not a deploy.
- `messages` — one per rendered notification, with its delivery state.
- `deliveries` — **append-only** attempt ledger: every provider attempt, its outcome, its error.
- `preferences` — per-recipient, per-channel suppression. Not decoration: sending WhatsApp to
  someone who opted out is a regulatory problem, not a bad user experience.

Three decisions worth the words:

**Rendering is strict.** An unresolved `{{placeholder}}` raises rather than rendering the literal
braces or an empty string. A customer receiving "Dear {{name}}" is worse than a message that
never left, because the second one is visible in a queue and the first is visible to the customer.

**Sending happens outside the event transaction.** The consumer's job ends when the message row
is committed as `pending`. A separate dispatcher claims pending rows with `FOR UPDATE SKIP
LOCKED` and talks to the provider — the same shape as `OutboxRelay`, for the same reason: network
I/O inside the transaction that reacts to an event makes a slow provider into a database problem.

**The transport is a port.** `SmtpTransport`, `WhatsAppCloudTransport` and `LogTransport` satisfy
one protocol. Tests run against a recording fake and never open a socket; local development uses
MailHog, which `make infra` already starts.

## 2. Lead and pipeline (done)

`lead` is built, as the third process of the `crm` product beside `customer` and `deal`, and
`pipeline` was deleted rather than built
([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)): `deal` already owns
`crm.pipeline.*` and publishes `crm.pipeline.deal.created` and `crm.pipeline.deal.stage_changed`.
Two services claiming one bounded context is how a second source of truth is born, so there is one
service, `lead`, and a removal. [crm.md](crm.md) describes what exists.

`lead` stores a lead that a signed-in user creates by hand or through the app's import (each tagged
with a source of `form`, `import` or `manual`; there is no public web-form or webhook capture yet,
which the programme plans as R3.7b, [crm.md](crm.md) section 11), assigns it with a round-robin
rule, tracks a qualification status, and converts a qualified lead into a company, a contact and a
deal by calling `customer` and `deal` over their internal API; it publishes `crm.lead.converted`
with the three ids.

The reason it is worth building rather than folding into `deal`: a lead is not a small deal. It
has no amount, no close date and no committed customer record, and most of them are discarded.
Modelling them as deals pollutes every pipeline metric the CRM sells.

## 3. Analytics and reporting (planned)

There is no analytics service and there should not be one that owns a database. The reporting
question the sales list asks — *"dashboards across sales, marketing and revenue"* — is a read
across six schemas, and the rule that no service reads another's tables is the one that keeps a
database-per-service split a configuration change.

So: a **projection service** that consumes the events it needs and maintains its own rollups,
exactly as `console` already does for commerce. It owns its schema, it holds nobody's truth, and
it can be rebuilt from the event stream. The alternative — a reporting service with cross-schema
reads — trades an irreversible architectural guarantee for a quarter of saved work.

## 4. The remaining scaffolds

`quotation`, `config` and `eventbridge` are still the generated template. `activity` was deleted
([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)), and `scheduler` and
`document` are built ([ADR-0015](adr/0015-scheduler-and-not-before-delivery.md),
[ADR-0009](adr/0009-uploaded-images-and-object-storage.md)). None of the scaffolds is load-bearing
for a sales claim, so none is urgent, but two of the built services are worth naming:

- `scheduler` is what turns "send a reminder three days before the invoice is due" into a thing
  the platform can do. Notification rules react to events; a reminder reacts to *time passing*,
  which no event describes. It is built; whether notification's recurring sends use it is not
  recorded here.
- `document` is the attachment story for both email and WhatsApp. It is built; what notifications
  attach is not recorded here.

## What this is not allowed to cost

- **No cross-schema foreign keys, and no cross-schema reads.** A notification rule names an event
  type and a payload path. It does not join to `support.tickets`.
- **No sending inside a consumer transaction.** The dispatcher exists precisely so a provider
  outage cannot hold a database transaction open.
- **No unrendered placeholder reaching a provider.** Strict rendering, asserted by a test.
- **No suppressed recipient receiving anything.** Preference is checked at render time and again
  at dispatch, because a rule can be edited between the two.
- **No provider credentials in the repository.** Transport configuration is environment, and the
  default transport is the one that writes to a log.
