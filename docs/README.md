# OpsGrids docs

Start here.

### Getting oriented

| If you want to… | Read |
|---|---|
| Get it running on your machine | [getting-started.md](getting-started.md) |
| Join the team and pick up your first task | [onboarding/](onboarding/README.md) |
| Understand how the system fits together | [architecture.md](architecture.md) |
| Know what exists, how it is built and what it exposes | [../SPEC.md](../SPEC.md) |
| Know what the platform can and cannot do today | [features.md](features.md) |
| Know what was decided and what already broke once | [../MEMORY.md](../MEMORY.md) |
| Pick up where the last session stopped | [../HANDOVER.md](../HANDOVER.md) |

### Building on it

| If you want to… | Read |
|---|---|
| Call the API | [api-reference.md](api-reference.md) |
| Know what is wrong with the API and what to fix | [api-improvements.md](api-improvements.md) |
| Write or run tests | [testing.md](testing.md) |
| Know which check enforces what, and run it | [TOOLCHAIN.md](TOOLCHAIN.md) |
| Package and price something — bundles, prepaid packs, seat classes, reseller pricing | [commerce-packaging.md](commerce-packaging.md) |
| Use or extend the visual system | [../frontend/CLAUDE.md](../frontend/CLAUDE.md) · `frontend/opsgrids-ui` |
| Run the screenshot, accessibility and layout-sweep checks | [../frontend/VISUAL-TESTING.md](../frontend/VISUAL-TESTING.md) |
| Understand the CRM as it is and as the overhaul changes it | [crm.md](crm.md) |
| Design against the approval engine's schema | [approval-engine-data-model.md](approval-engine-data-model.md) |
| Know what Engage guarantees on contact erasure and unsubscribe links | [engage-erasure-and-unsubscribe.md](engage-erasure-and-unsubscribe.md) |
| Know why no network call runs inside a database transaction | [io-outside-transactions.md](io-outside-transactions.md) |

### Deciding what to do next

| If you want to… | Read |
|---|---|
| Know what is next and what is blocked | [backlog.md](backlog.md) |
| Know which sales claims the code actually supports, and what closing the rest costs | [ecosystem-gaps.md](ecosystem-gaps.md) |
| Understand the longer arc and its sequencing | [plan.md](plan.md) |
| Know what is still risky in production | [production-readiness.md](production-readiness.md) |
| Understand why something is shaped the way it is | [decisions.md](decisions.md) · [adr/](adr/) |
| Know whether an ADR question is decided, and which backlog item builds it | [adr/open-questions-register.md](adr/open-questions-register.md) |
| Learn from what has already gone wrong | [discoveries.md](discoveries.md) |
| Run it, or work out why it is misbehaving | [operations.md](operations.md) · [../infrastructure/edge/RUNBOOK.md](../infrastructure/edge/RUNBOOK.md) |
| Filter the product catalogue (proposal) | [subcategory-filter-plan.md](subcategory-filter-plan.md) |

## The one-paragraph version

OpsGrids is one platform selling many products. A customer has **one organization, one account
and one user directory**, and activates products — HRMS, CRM, PulseBoard, and others — against
it. The backend is Python microservices, each owning its own schema, sitting behind two
gateways: `app-gateway` for product UIs and `admin-gateway` for staff and internal calls.

## What to read first, and why

If you are about to write a service, read [ADR-0000](adr/0000-phase-0-decisions.md) before
anything else. It records eleven decisions that are cheap now and very expensive later — token
shape, permission grammar, money representation, tenancy model. They are not open for casual
revision, and several of them are load-bearing for code you are about to write.

If you are about to touch anything that reads or writes tenant data, also read
[ADR-0002](adr/0002-row-level-security.md). It is accepted and built: every tenant table has a
forced row-level security policy, driven by a transaction-local GUC. It also explains why that
policy is a net, not the control. The filter on every query is still the control, and a policy
that is misconfigured fails silently by returning zero rows. Knowing that changes how carefully
you write the query.

## Honesty policy for these documents

These pages distinguish three states, and the distinction is the point:

- **Built** — exists, has tests, and the tests were run.
- **Partial** — exists but is not wired end to end, or is not covered.
- **Absent** — named in a plan or an ADR, and not written.

A thing named in an ADR is a decision, not an implementation. Where these documents and the
code disagree, the code is right and the document is a bug — please fix it.

Two pages are worth reading even if you think you know this codebase:
[discoveries.md](discoveries.md), because every defect found here passed a green test suite, and
[production-readiness.md](production-readiness.md), because production is live and the open risks
are specific: no off-box backup, a memory budget over the box, CI not running.

### Other references

- [Inventory & manufacturing plan](inventory/plan.md) · [ADR-0014](adr/0014-inventory-and-manufacturing.md)

### Historical — kept for the record, not current

| Page | Superseded by |
|---|---|
| [what-was-built.md](what-was-built.md) | [../SPEC.md](../SPEC.md), [../MEMORY.md](../MEMORY.md) |
| [theme-guide.md](theme-guide.md), [existing-theme-features.md](existing-theme-features.md) | [../frontend/CLAUDE.md](../frontend/CLAUDE.md), `frontend/opsgrids-ui` |
| [architecture.html](architecture.html) | [../SPEC.md](../SPEC.md) |
