# Provider templates

How to send a WhatsApp notification the customer did not just ask for.

## Why this exists

Meta accepts free text only inside a **24-hour customer service window** that opens when the
customer messages you. Outside it, *"template messages are the only type of message that can be
sent to WhatsApp users"*. Every automation worth having — an invoice reminder, an order update, a
ticket acknowledgement — is outside that window by definition, because the customer did not just
write to you.

So a proactive WhatsApp notification is a template message, and nothing else is.

## Two kinds of template, and why both exist

| | `templates` | `provider_templates` |
|---|---|---|
| Who owns the wording | We do | Meta does |
| How it is sent | Rendered here, sent as free text | Registered at Meta; we send parameters |
| Reviewed | No | **Yes — Meta approves or rejects it** |
| Valid outside the 24-hour window | No | Yes |
| Used for | Email; WhatsApp replies inside a window | Every proactive WhatsApp notification |

A rule names **exactly one** of them. Both would be ambiguous about which wording the customer
reads; neither leaves a rule that fires and renders nothing.

`provider_templates` is not called `whatsapp_templates` on purpose. The register-then-reference
lifecycle is not WhatsApp's alone — SMS in India carries DLT-registered templates with the same
shape — so the channel is a column.

## Registering one

Two steps, and the first is not ours.

**1. Create the template at Meta and wait for approval.** Nothing here can shorten this. Until
Meta returns `APPROVED`, the template cannot be sent, and this service refuses to try.

**2. Register it locally**, mapping Meta's parameter positions to our variable names:

```http
PUT /api/v1/notifications/provider-templates
{
  "code": "invoice_due_reminder",
  "channel": "whatsapp",
  "provider_name": "invoice_due_reminder",
  "language": "en_US",
  "category": "utility",
  "parameter_format": "positional",
  "status": "approved",
  "variables": [
    { "key": "customer_name",  "component": "body",   "position": 0 },
    { "key": "invoice_number", "component": "body",   "position": 1 },
    { "key": "amount_due",     "component": "body",   "position": 2 },
    { "key": "payment_link",   "component": "button", "button_index": 0, "kind": "text" }
  ],
  "preview_text": "Hi {{1}}, invoice {{2}} for {{3}} is due. Pay: {{4}}"
}
```

`key` is **our** vocabulary — the same name a rule's payload path resolves — and `position` is
Meta's. Keeping both in this row is what lets the rest of the service speak one language while
the provider's ordering stays contained here.

Then point a rule at it:

```http
PUT /api/v1/notifications/rules
{
  "code": "remind_due_invoices",
  "event_type": "finance.billing.invoice.issued",
  "channel": "whatsapp",
  "provider_template_code": "invoice_due_reminder",
  "recipient_path": "customer.phone",
  "category": "billing",
  "dedupe_key_path": "invoice.number"
}
```

## What is validated, and when

Everything that can be checked before a customer is involved, is:

| Check | When | Why then |
|---|---|---|
| Component is body/header/button | Authoring (422) | A typo is an operator's mistake |
| Positions contiguous from zero | Authoring (422) | **See below — the important one** |
| No two variables in one slot | Authoring (422) | Ambiguous ordering |
| Literal recipient is valid E.164 | Authoring (422) | A typo would be a dead send |
| Template exists | Rule authoring (422) | A rule pointing at nothing fires forever and sends nothing |
| Template is `approved` | Send time (skipped, logged) | Status changes after authoring; Meta may pause a template |
| Every variable has a value | Send time (skipped, logged) | Depends on the event payload |

**The position rule is the one that matters most.** Meta fills parameters in order. A gap does
not leave a hole — it shifts every later value one place earlier, so `{{3}}` receives what
`{{2}}` was meant to get. The message sends successfully and carries the wrong data, to a real
customer, in a channel they trust. That is why positions must be contiguous from zero and why it
is refused at authoring rather than reported later.

## Named parameters

Meta templates may declare **named** parameters (`{{customer_name}}`) instead of positional
(`{{1}}`). Both are supported, and the difference is confined to the template row.

The mapping layer is the template's `variables`:

| Field | Whose name | Used by |
|---|---|---|
| `key` | **ours** — the dotted path a rule's payload resolves | both formats |
| `position` | Meta's — order within the component | positional only |
| `parameter_name` | Meta's — the label | named only |

So the ERP writes `{{invoice_number}}` against its own event payloads and Meta receives either
`BODY` parameter 1 or `parameter_name: "invoice_number"`. Nothing outside the template row knows
which form the provider wanted.

A named send looks like this, and the field names were read off Meta's own example:

```json
"parameters": [
  { "type": "text", "parameter_name": "customer_name",  "text": "Priya Nair" },
  { "type": "text", "parameter_name": "invoice_number", "text": "INV-9" }
]
```

Note the **lowercase** `"text"`. A third-party summary of the same feature showed it uppercase;
following that would have produced a payload Meta rejects, which is the `sub_type` mistake a
second time. The rule stands: field names come from Meta's pages or they do not get written.

### What each format validates, and why they differ

| Format | Checked | Failure it prevents |
|---|---|---|
| positional | positions unique and contiguous from zero | Meta fills in order, so a gap shifts every later value into the wrong placeholder — a message that sends successfully carrying the wrong data |
| named | every non-button variable has a valid, unique `parameter_name` | Meta matches by label, so a missing or misspelled name leaves that placeholder **unfilled** while the send reports success |

The two failure modes are opposites, which is why the validators are separate. A positional
template is wrong loudly; a named one is wrong quietly. Applying the positional checks to a named
template would also be wrong in a smaller way — named values may arrive in any order, so
requiring contiguous positions would force an operator to invent numbers that mean nothing.

### Buttons

Button parameters keep the positional shape in **both** formats:

```json
{ "type": "button", "sub_type": "url", "index": "0",
  "parameters": [{ "type": "text", "text": "9" }] }
```

Meta publishes `parameter_name` for header and body components and publishes no named form for a
button, which `index` already identifies unambiguously. Emitting an unverified `parameter_name`
there would be inventing a payload; omitting it leaves the shape Meta does document. If that
turns out to be wrong the 4xx is classified permanent and visible, rather than silently filling
the wrong button.

## What a message records

When a rule sends a provider template, the `messages` row keeps:

- `provider_template_code` — which template
- `provider_template_payload` — the **exact** parameter object handed to Meta
- `body` — the provider's `preview_text`, **unsubstituted**

That last one is deliberate and worth knowing. The provider owns the wording and performs the
substitution, so a locally-rendered guess at the result would be text we never actually sent.
The parameters we did send are in `provider_template_payload`, which together with the preview is
a faithful record. A future inbox can compose the two for display.

## Verified against

Field names here were read off Meta's published example rather than recalled, on 2026-09-19:

- [Template fundamentals](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview) — categories, named vs positional, approval statuses, the window rule
- [Media card carousel templates](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/marketing-templates/media-card-carousel-templates) — the literal `components`/`parameters` payload, and `index` as a **string**
- [Cloud API messages reference](https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages/) — `POST /{Version}/{Phone-Number-ID}/messages`

The dedicated send-message-templates guide returned HTTP 500 throughout; the shapes above come
from the carousel page's complete example, which includes body, header and both button subtypes.
