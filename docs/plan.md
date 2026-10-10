# Plan

Where this is going and in what order. For the immediate queue see [backlog.md](backlog.md);
this page is the longer arc and the reasoning behind the sequence.

---

## The goal

One platform selling many products. A customer has **one organization, one account and one user
directory**, and activates products against it — HRMS, CRM, PulseBoard, Project
Management, IT Management, Marketing, Finance, Operations. Centralised billing, usage metering,
an operator portal and a product marketplace sit across all of them.

Everything the customer sees as "adding a product" should be an activation, never a new account,
never a re-upload of their people.

## What is being replaced

| Existing | Disposition |
|---|---|
| **HRMS** — FastAPI, 10 services | Requirements source. Domain logic **re-implemented**, not lifted |
| **PulseBoard** — Next.js/Prisma, ~1,915 tests | **Data migrates** with preserved UUIDs. Frontend survives as a client |
| **CRM_2.0 / Sales_CRM** — Laravel | Out of scope. CRM is built fresh |

The existing codebases are the **specification**, and their test suites are the acceptance
criteria for the rebuild. Read them; do not port them.

## Phases

| # | Phase | Delivers | State |
|---|---|---|---|
| 1 | Platform foundation | `platform-core`, dev bootstrap, CI, service generator | **Done** |
| 2 | Identity & org | auth, account, both gateways | **Done** |
| 3 | Product registry | `product-service`, `config-service`, real entitlement gate | Next |
| 4 | Platform services | document, notification, audit, scheduler, eventbridge | |
| 5 | HRMS rebuild | employee → attendance → leave → payroll → recruit | |
| 6 | PulseBoard rebuild + migration | project, timesheet, standup, quotation | |
| 7 | CRM | customer, deal, lead: one product, `crm`, three services ([ADR-0017](adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)); marketing and support are separate products | |
| 8 | Billing | catalog → subscription → metering → billing → payment | |
| 9 | Ecosystem surface | operator console, marketplace, workflow, analytics | |

Interleaved and **not optional**: row-level security ([ADR-0002](adr/0002-row-level-security.md))
and the end-to-end suite. Both are listed first in [backlog.md](backlog.md) and both are
prerequisites rather than improvements — every product service adds tables to retrofit, and every
service pair adds a contract nothing tests.

## Sequencing decisions worth knowing

**Start emitting usage events in phase 3 with `is_billable=false`.** Turning on usage-based
pricing later then becomes plan configuration rather than an architecture change. It costs almost
nothing now and is very expensive to retrofit.

**Build the Razorpay adapter before Stripe.** Razorpay is the more constrained provider — mandate
caps, a legally required pre-debit notice, multi-day eMandate activation, no true idempotency
key. An interface designed Stripe-first produces abstractions Razorpay cannot satisfy, discovered
after it has ten call sites.

**Phase 6 is the riskiest.** It migrates live customer data. It needs a full backup, checksum
verification per table, and a rollback that has actually been **rehearsed** — not merely written
down.

**Nothing after phase 2 is on the critical path for everything else.** Product tracks can run in
parallel once the platform exists — which is the entire point of having built the platform first.

## Adding a product — the checklist this architecture exists to make true

1. Reserve a product key; row in `product-service`.
2. Claim URL prefixes in the gateway route table — **config, never a code edit**.
3. Publish a permission manifest; account-service registers it.
4. Ship role templates the platform instantiates per organization.
5. Declare plan features and usage metrics to `catalog-service`.
6. Declare the event catalogue as JSON Schema.
7. Declare projections and register reconcilers.
8. Register notification templates and file scopes.
9. Emit audit through the shared mixin — never a bespoke log table.
10. Publish OpenAPI into the docs hub.
11. Implement lifecycle consumers: access granted and revoked, membership revoked, org suspended.
12. State the data contract on revocation and deletion.
13. Meet the test bar: negative-authz per endpoint, idempotency per consumer, drift per
    projection, contract per event.
14. Add it to the marketplace.

Steps 2, 3 and 6 are the ones that keep a product launch from editing core code. Where a step is
impossible today, that is a gap in the platform rather than a reason to special-case the product.

## Cost, stated plainly

This is a **multi-year programme**, not a quarter. Roughly 34 services eventually, rebuilding two
working products and building a third, plus billing and a platform underneath. Phases 1–4 are the
platform, and deliver no user-visible product feature.

Rebuilding working software has no visible payoff until it reaches parity. HRMS passes 146 tests
today; PulseBoard passes around 1,915. Every one of those is a behaviour somebody must re-specify.

Operating this many services means Kubernetes, RabbitMQ, DLQ monitoring, tracing and somebody on
call. Budget for a platform team whose product *is* the platform, or the services will be built
and then not run well.

**Everything is reversible except the ADR-0000 decisions.** Token format, permission grammar,
identity shape, event envelope and money representation are the genuinely irreversible ones.
