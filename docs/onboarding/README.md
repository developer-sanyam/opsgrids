# Onboarding

How four developers work on this platform without holding it in their heads.

| Document | Who reads it |
|---|---|
| [`git-workflow.md`](git-workflow.md) | every developer, before their first card |
| [`never-do.md`](never-do.md) | every developer — print it |
| [`cards-dev-a-lead.md`](cards-dev-a-lead.md) | Dev A — `services/lead` |
| [`cards-dev-b-pipeline.md`](cards-dev-b-pipeline.md) | Dev B — `services/pipeline` |
| [`cards-dev-c-hrms.md`](cards-dev-c-hrms.md) | Dev C — `frontend/product-hrms` |
| [`cards-dev-d-crm.md`](cards-dev-d-crm.md) | Dev D — `frontend/product-crm`, `frontend/atmora-ui` |

---

## How this works

Work is handed out as **cards**. One card is one branch, one pull request, roughly one day.

A card is not a ticket. A ticket says what to achieve; a card says what to type. Every card
carries **the contract** — the exact column names, permission strings, endpoint paths or props
— and **a reference file** that already does the same thing correctly. The developer copies the
reference and substitutes the contract. They are not asked to design, and they must not.

This works on this codebase specifically because:

- **Every service has the same anatomy.** `CLAUDE.md`: *"Learn one, navigate all of them."*
  `services/standup` is the smallest complete one and is the reference for both backend lanes.
- **The quality gate is a referee, not a matter of opinion.** `make gate` is pass/fail, and
  every card ends with the exact commands to run. "Am I done?" is answered by a command.
- **Contracts are generated and committed.** `make openapi-check` fails CI when
  `libs/contracts/` drifts from the code, so alignment is enforced by a machine.
- **Layering is machine-enforced.** `make boundaries` catches the most common mistake — a
  router importing `models` instead of going through `selectors`.

## The rule that carries the most weight

> **If the card does not say it, do not decide it. Comment on the card and wait.**

Every failure mode this structure exists to prevent — an invented column, a forked component, a
hand-edited contract, a permission that belongs to nobody — is a variant of guessing. Nobody on
this team will be annoyed that you asked.

## Lanes

Each developer owns a disjoint set of files. No two open cards ever touch the same file.

| Dev | Owns | Never touches |
|---|---|---|
| A | `services/lead/**` | any other service |
| B | `services/pipeline/**` | any other service |
| C | `frontend/product-hrms/**` | other apps, `atmora-ui` |
| D | `frontend/product-crm/**`, `frontend/atmora-ui/**` | other apps |

A and B are building the two halves of the **Sales CRM** product, which is currently broken:
the gateway gates `/api/v1/leads`, `/api/v1/pipelines` and `/api/v1/activities` on a product
that has no permission manifest behind it. They run the same five cards against different
services, from the same reference, so by the end of the fortnight either can read the other's
code.

`atmora-ui` is shared by every app, so **only one open card may touch it at a time** — Dev D's.

## Before day one

Setup is not a card. Each developer follows the environment guide until `make check` is green
on their machine, and only then picks up card 1.

> `docs/getting-started.md` is out of date — it names Python 3.13, claims no event consumers are
> registered, and says `atmora-ui` and `atmora-client` have empty `src/` directories. All three
> are false. Use the current setup guide instead, and treat `README.md` and `CLAUDE.md` as
> authoritative.

## For the reviewer

Six questions per pull request. If all six pass, merge — the gate has already checked that the
code works.

1. Does the diff touch **only** the files the card listed?
2. Is the DONE WHEN output pasted, and is CI green?
3. If `app/schemas.py` or `app/catalogue.py` changed, is the regenerated `libs/contracts/`
   committed in the same pull request?
4. Does every new endpoint have its negative-authz tests — 401, 403 without permission, 402
   without entitlement, 403 out-of-scope write?
5. Did they invent anything the card did not specify?
6. Frontend only: all four states present, and `atmora-ui` un-forked?

Squash-merge into `development`. Promote `development` → `main` when a slice is coherent.

**The signal to watch in the first week** is not how many cards land. It is how many times a
developer guessed instead of commenting. A card that produced a guess was an under-specified
card — fix the card, not the developer.
