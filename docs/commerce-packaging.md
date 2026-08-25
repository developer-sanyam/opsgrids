# Commerce packaging

How the platform sells things, as distinct from how it bills them. Four packaging shapes the
market asks for — bundles, prepaid packs, free view-only seats, and reseller pricing — over the
commerce services [ADR-0004](adr/0004-commercial-model.md) already put in place.

The headline is that three of the four are **configuration**. That is not a happy accident; it is
the payoff of two decisions made on day zero and never bent since: a plan version is immutable and
a subscription pins one ([ADR-0000](adr/0000-phase-0-decisions.md) §9), and the credit ledger is
append-only with the balance derived rather than stored (ADR-0004 §3). Almost everything a sales
team means by "packaging" turns out to be a *price* or a *credit*, and the platform already has
somewhere honest to put both.

| Shape | Configuration or code | Why |
|---|---|---|
| **Graduated bundle discounts** | Configuration | A bundle is a plan version. Its price sheet is the bundled price and its `features` name its members and its rung. |
| **Prepaid credit packs** | Configuration, plus one correction | A pack is a time-boxed credit grant. What needed code was `drawn_from`: a draw-down has to inherit the pack's window, or an expiring pack drags the balance negative. |
| **Free view-only seat class** | **Code** — one new table | Nothing in a price sheet could say a plan sells two kinds of seat, still less that one of them may not write. |
| **Partner / reseller pricing** | Configuration | `restricted_to_org_id` pointed at the reseller. The same audience-of-one mechanism a negotiated enterprise price uses. |

---

## 1. Graduated bundle discounts

**A bundle is a plan version, not a discount layered over one.** That distinction is the whole
design. A discount flag would have to be re-evaluated every time an invoice was reconstructed, and
it would be evaluated against whatever the discount rules said *then* rather than what they said
when the customer signed. A bundle version is frozen the moment it is published, so the line item
that names it in `plan_version_id` can be re-derived exactly, years later, from rows nobody may
edit.

### Authoring one

An ordinary `POST /api/v1/admin/catalog/plans/{id}/versions` with two feature rows:

```json
{
  "currency": "INR",
  "prices": [{ "kind": "per_seat", "amount_minor": 80700, "currency": "INR", "unit": "seat" }],
  "features": [
    { "feature_key": "bundled_products", "value": ["hrms", "crm", "finance"] },
    { "feature_key": "bundle_tier", "value": "trio" }
  ]
}
```

No new field, no new endpoint. `bundled_products` and `bundle_tier` are the only two feature keys
the *platform* reads rather than a product — everything else in `features` stays a product's own
vocabulary that catalog never interprets.

### The seeded ladder

Migration `f78c86fcec78` seeds the ladder under the `suite` product key, priced per seat per
month in paise:

| Rung | Products | Bundled | Bought apart | Saving | Per product |
|---|---|---|---|---|---|
| `duo` | 2 | 58,200 | 64,800 | 6,600 | 29,100 |
| `trio` | 3 | 80,700 | 89,700 | 9,000 | 26,900 |
| `full` | 4 | 95,600 | 1,09,600 | 14,000 | 23,900 |

"Graduated" is the last column: each further product brought into the bundle lowers the price of
*every* product in it. `domain.ladder_is_graduated` checks that by cross-multiplying rather than
dividing — `a.total × b.products < b.total × a.products` — because dividing a total by a product
count rounds, and a rounded comparison of two prices is a comparison of two other prices. The test
reads the seed migration's own numbers, so re-pricing a rung into a ladder that gets *dearer* per
product fails the build and names the rung.

### What a bundle does not yet do

**It prices the packaging; it does not fan entitlement out to its members.** A subscription
entitles one `product_key`, and a subscription to a bundle entitles the bundle's key. Making a
`suite/trio` subscription light up `hrms`, `crm` and `finance` means the product-service
projection reading `bundled_products` off the subscription event and upserting one activation per
member — real code in the entitlement path, in three services, which is squarely outside a
packaging slice. It is on the backlog. `bundled_products` exists now precisely so that work has a
contract to read rather than a convention to guess.

---

## 2. Prepaid credit packs

**A pack is a credit grant with a window.** Nothing more:

```http
POST /internal/v1/credit
{ "organization_id": "…", "amount_minor": 12000000, "currency": "INR",
  "reason": "Annual prepaid commitment",
  "valid_from": "2026-04-01T00:00:00Z", "valid_until": "2027-04-01T00:00:00Z" }
```

Each invoice then draws against it, and each draw is another append-only row:

```http
POST /internal/v1/credit:consume
{ "organization_id": "…", "amount_minor": 1000000, "currency": "INR",
  "reason": "INV-2026-04", "drawn_from": "<the pack's entry id>" }
```

What is left is `GET /internal/v1/organizations/{org}/credit/packs/{pack}` — a `SUM` over the
grant and everything drawn from it, never a column. A billing run asks it *before* drawing, so
"the pack covers ₹25,000 of this ₹30,000 invoice and ₹5,000 is payable" is an ordinary answer
rather than a caught exception.

### The one thing that needed code, and why

`drawn_from` is new, and it is not a convenience. Before it, every consumption was windowed
`[now, NULL)` — open-ended. Against an *open-ended* grant that is fine. Against a **time-boxed**
pack it is a bug with a long fuse: the instant the pack's `valid_until` passes, the grant drops
out of the balance `SUM` while the open-ended consumption keeps counting, and the organization's
balance reads *minus what was spent* — for ever, quietly eating unrelated credit.

This is the same defect the reversal path was fixed for (a reversal inherits the window of the
entry it negates, `c9d2e4f7a1b3`), arriving from the other direction, and it takes the same
answer: a draw-down names its pack and inherits that pack's `[valid_from, valid_until)`, so the
pack and everything spent from it net to zero at every instant the balance can be asked about. A
reversal of a draw carries `drawn_from` across too, or the pack's own remainder would disagree
with the ledger it is computed from.

Five things a draw refuses, each a way a pack goes wrong quietly rather than loudly: another
tenant's pack (a 404 under row-level security), a source that is not a grant, a currency mismatch,
a lapsed pack, and an overdraw. None of them weakens append-only — every one of them is a refusal
before a row is written.

### The UPI Autopay ceiling

A pack is also the answer to a payments constraint. Razorpay's UPI Autopay mandates carry a
per-debit cap — commonly **₹15,000 per cycle** — and `payment.create_charge` refuses an amount
above a mandate's `max_amount_minor` rather than letting the provider decline it later.

So an annual commitment of ₹1,20,000 collected as a mandate debit does not "mostly work". It
fails, and a failed collection opens a dunning cycle ([ADR-0005](adr/0005-dunning-and-grace.md))
against a customer who agreed to pay a year up front and did nothing wrong. Collected once as a
prepaid pack, **no debit is raised at all**: there is no charge to exceed a cap, so there is no
failed collection to dun. Twelve monthly invoices of ₹10,000 — each comfortably under the ceiling
in its own right, which is the point — draw the pack down to exactly zero. The thirteenth finds it
empty and is payable, which is the moment the customer is asked to commit again.

---

## 3. Seat classes — the one that needed a table

A `per_seat` price says what a seat costs. Nothing said that a plan sells **two kinds of seat**,
and nothing at all said that one of them may not write. HubSpot made the free view-only seat the
market's default expectation; the catalogue had no way to express it.

The tempting non-answer was a `per_seat` price of zero. It would have priced the free seat
correctly and entitled it wrongly — and the entitlement is the expensive half.

`catalog.seat_classes` hangs off a plan version:

| Column | Means |
|---|---|
| `code`, `name` | `member`, `viewer`, `approver`. Unique within the version. |
| `price_id` | The version's own `per_seat` price this class is charged at, or **NULL for free**. Free is the absence of a price row, never a zero-amount one. |
| `included_count` | Seats of this class the plan includes at no extra charge; NULL is unlimited. |
| `can_write` | Whether a seat of this class may hold a writing permission **at all**. |

Money stays in `prices` and exists in exactly one place, so a seat class and its rate can never
disagree. The row is frozen with its version by the same trigger that freezes prices and
features: what a seat may do is a term of the deal, and flipping `can_write` on a published
version would silently widen every seat already sold under it.

### The entitlement implication is computed, not displayed

`domain.seat_class_grants(seat_class, catalogue)` derives the permission codes a seat of a class
may hold. For `can_write=False` the filter is a **whitelist of read actions** (`read`, `list`,
`view`) with dangerous permissions excluded — so:

- no `.write` survives it;
- `read_sensitive` does not survive it either, though it reads, because it unmasks PII and
  "view-only" is a statement about blast radius rather than about the verb;
- a verb nobody has classified yet — `approve`, `export`, `impersonate` — is **excluded by
  default** rather than falling through, which is the safe direction for a seat class whose entire
  promise is that it cannot change anything.

A writing class gets the catalogue as published: *which* role a paying seat actually holds is the
product's business, and the seat class only decides whether writing is possible at all.

This is deliberately a function and not a screen. A UI gate hides a control; it does not protect
data. A view-only seat is only view-only if the grant computed for it never contains a write, so
that is what the tests assert.

---

## 4. Partner and reseller pricing

Nothing new at all. A reseller price is a plan version with `restricted_to_org_id` set to the
reseller's organization:

```json
{
  "currency": "INR",
  "restricted_to_org_id": "a17e5e11-0000-4000-8000-000000000001",
  "prices": [{ "kind": "per_seat", "amount_minor": 68595, "currency": "INR", "unit": "seat" }],
  "features": [{ "feature_key": "partner_of", "value": "a17e5e11-…" }]
}
```

That is the same audience-of-one mechanism ADR-0004 §2 built for a negotiated enterprise price.
Channel distribution turned out to be the same problem wearing a hat: one organization, one
private price, invisible to everybody else. It inherits the guarantee and the guard — the
`selectors._audience_ok` filter that stands in for row-level security on a global table, and the
negative-authz test that proves another organization cannot see it. A leak here shows one customer
another's channel margin.

`partner_of` is documentation on the row, so an operator screen can say "partner price" instead of
inferring it from a bare UUID. **The enforcement is `restricted_to_org_id`**, and adding a
`partner_of` feature to a version that is not restricted grants nobody anything.

A partner price is *not* a further rung of the bundle ladder — it is the same rung at a wholesale
rate — which is why `ladder_is_graduated` is computed over public versions only.

---

## What packaging is not allowed to cost

Every one of these shapes had a shortcut that would have made it easier and would have broken
something load-bearing. For the record, and so the next one is refused for the same reasons:

- **A mutable "bundle discount %" on a plan.** Re-prices everyone on it and makes a past invoice
  impossible to reconstruct. A bundle is a version.
- **A `remaining_minor` column on a credit pack.** A second source of truth that drifts from the
  ledger the first time two invoices draw on it at once. Remaining is a `SUM`.
- **A zero-amount `per_seat` price standing in for a free seat class.** Prices the seat right and
  entitles it wrong.
- **A `view_only` flag read by the frontend.** Authorization is server-side. The grant is computed
  from the seat class or the seat class means nothing.
- **Percentages, ratios or floats anywhere in the money.** Bundle savings and per-product
  comparisons are integer arithmetic on minor units; the ladder comparison cross-multiplies rather
  than dividing.
