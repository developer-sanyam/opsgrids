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

## Named parameters are not supported yet

Meta templates may declare **named** parameters (`{{customer_name}}`) instead of positional
(`{{1}}`). Registering one is allowed here; **sending it is refused** with an explicit error.

The reason is deliberate. Meta's documentation establishes that named parameters exist and how
they are declared *at template creation*, but the shape a named parameter takes in the **send**
payload is not published on any page we consulted. Guessing it has two outcomes: the send is
rejected, or — far worse — it is accepted with values landing in the wrong placeholders. So a
named template fails loudly with an instruction rather than being sent on a guess.

To finish it: confirm the send-side named-parameter object against Meta's reference, then extend
`domain.build_template_payload`. The test that pins the refusal will tell you where.

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
