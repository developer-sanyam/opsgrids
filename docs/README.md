# Atmora docs

Start here.

### Getting oriented

| If you want to… | Read |
|---|---|
| Get it running on your machine | [getting-started.md](getting-started.md) |
| Understand how the system fits together | [architecture.md](architecture.md) |
| See the whole system on one page | [architecture.html](architecture.html) |
| Know what exists and how far it is trusted | [what-was-built.md](what-was-built.md) |
| Know what the platform can and cannot do today | [features.md](features.md) |

### Building on it

| If you want to… | Read |
|---|---|
| Call the API | [api-reference.md](api-reference.md) |
| Know what is wrong with the API and what to fix | [api-improvements.md](api-improvements.md) |
| Write or run tests | [testing.md](testing.md) |
| Package and price something — bundles, prepaid packs, seat classes, reseller pricing | [commerce-packaging.md](commerce-packaging.md) |
| Use or extend the visual system | [theme-guide.md](theme-guide.md) |
| Know what the theme already provides | [existing-theme-features.md](existing-theme-features.md) |

### Deciding what to do next

| If you want to… | Read |
|---|---|
| Know what is next and what is blocked | [backlog.md](backlog.md) |
| Understand the longer arc and its sequencing | [plan.md](plan.md) |
| Know whether this can be deployed | [production-readiness.md](production-readiness.md) |
| Understand why something is shaped the way it is | [decisions.md](decisions.md) · [adr/](adr/) |
| Learn from what has already gone wrong | [discoveries.md](discoveries.md) |
| Run it, or work out why it is misbehaving | [operations.md](operations.md) |
| Filter the product catalogue (proposal) | [subcategory-filter-plan.md](subcategory-filter-plan.md) |

## The one-paragraph version

Atmora is one platform selling many products. A customer has **one organization, one account
and one user directory**, and activates products — HRMS, CRM, PulseBoard, and others — against
it. The backend is Python microservices, each owning its own schema, sitting behind two
gateways: `app-gateway` for product UIs and `admin-gateway` for staff and internal calls.

## What to read first, and why

If you are about to write a service, read [ADR-0000](adr/0000-phase-0-decisions.md) before
anything else. It records eleven decisions that are cheap now and very expensive later — token
shape, permission grammar, money representation, tenancy model. They are not open for casual
revision, and several of them are load-bearing for code you are about to write.

If you are about to touch anything that reads or writes tenant data, also read
[ADR-0002](adr/0002-row-level-security.md), which is **proposed, not accepted**. It documents
the fact that tenant isolation currently rests entirely on every query carrying the right
filter. Knowing that changes how carefully you write the query.

## Honesty policy for these documents

These pages distinguish three states, and the distinction is the point:

- **Built** — exists, has tests, and the tests were run.
- **Partial** — exists but is not wired end to end, or is not covered.
- **Absent** — named in a plan or an ADR, and not written.

A thing named in an ADR is a decision, not an implementation. Where these documents and the
code disagree, the code is right and the document is a bug — please fix it.

Two pages are worth reading even if you think you know this codebase:
[discoveries.md](discoveries.md), because every defect found here passed a green test suite, and
[production-readiness.md](production-readiness.md), because the honest answer to "can we deploy
this" is currently no and the reasons are specific.
