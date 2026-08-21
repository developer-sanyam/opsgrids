# Atmora docs

Start here.

| If you want to… | Read |
|---|---|
| Get it running on your machine | [getting-started.md](getting-started.md) |
| Know what exists and how far it is trusted | [what-was-built.md](what-was-built.md) |
| Know what the platform can and cannot do today | [features.md](features.md) |
| Know what is next and what is blocked | [backlog.md](backlog.md) |
| Understand why something is shaped the way it is | [adr/](adr/) |
| See the whole system on one page | [architecture.html](architecture.html) |

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
