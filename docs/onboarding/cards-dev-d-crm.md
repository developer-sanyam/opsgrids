# Dev D — the CRM app and the design system

**You own `frontend/product-crm/` and `frontend/opsgrids-ui/`.**

Five cards, in this order. Each is roughly a day. Do not start a card until the previous one
is merged.

## What you are building, in plain language

Two of these cards write **tests for code that already works**. That is not busy-work: this
repository has 33 backend services with thousands of lines of tests, and a frontend with
exactly **one** test file in it. The functions you will be testing calculate money and pipeline
value. Nothing currently notices if someone breaks them.

The other three cards fix real defects in the CRM app.

You also own `opsgrids-ui`, the shared design system every app imports. **Only one person may
have an open change to it at a time, and that person is you.** An app may never copy a
component out of it — if a product needs a variant, the variant becomes a prop on the shared
component.

Read these once before card D1:

- `frontend/CLAUDE.md` — all of it
- `docs/onboarding/never-do.md`
- `docs/onboarding/git-workflow.md`

### How to run things

```bash
cd frontend && pnpm install                    # once
pnpm --filter @opsgrids/ui test                  # the design system's tests
pnpm --filter @opsgrids/product-crm test         # the CRM app's tests
pnpm --filter @opsgrids/product-crm dev          # http://localhost:4340
```

### A note on how tests work here

The tests you are writing are for **pure functions** — you give them an input, they return an
output, nothing else happens. No browser, no network, no database. That makes them the easiest
kind of test to write and the easiest to be sure about.

Your reference is `frontend/opsgrids-ui/src/lib/qr.test.ts`. Read its opening comment: it explains
*why* the test exists before it tests anything. Yours should too.

---

# CARD D1 — test the design system's formatters

**BRANCH** `feat/d1-ui-lib-tests`

**GOAL** Pin the behaviour of the four small functions every app in the suite depends on.

### REFERENCE

`frontend/opsgrids-ui/src/lib/qr.test.ts` — the only existing test in the frontend. Copy its
shape: a docstring explaining why, then `describe` / `it` blocks.

### FILES YOU WILL TOUCH

```
frontend/opsgrids-ui/src/lib/cn.test.ts        ← new file
frontend/opsgrids-ui/src/lib/format.test.ts    ← new file
```

Nothing else. You are not changing the functions, only describing what they already do.

### THE CONTRACT

**`cn.ts`** exports `cn(...parts)`, which joins class names and drops falsey ones. Test:

- several strings join with single spaces
- `false`, `null` and `undefined` are dropped
- all-falsey input returns `""`
- no input returns `""`

**`format.ts`** exports two functions.

`initials(name)` — test:

| Input | Expected |
|---|---|
| `"Sofia Marchetti"` | `"SM"` |
| `"Prakash"` | `"P"` |
| `"  Aditi   Rao  "` | `"AR"` — extra whitespace collapses |
| `"anita desai"` | `"AD"` — always uppercase |
| `"Jean-Luc Picard"` | whatever the function actually returns — run it and pin that |
| `""` | whatever it actually returns — run it and pin that |

`formatDelta(pct)` — test:

| Input | Expected |
|---|---|
| `4.2` | `"+4.2%"` |
| `0` | `"+0.0%"` |
| `-1.35` | `"−1.4%"` |
| `-0.04` | `"−0.0%"` |

**The most important assertion on this card:** the minus sign in a negative delta is the true
minus character `−` (U+2212), **not** the hyphen `-` you type on a keyboard. They look almost
identical and are different characters. Write at least one test that would fail if someone
replaced it with a hyphen, and say in a comment that this is deliberate — otherwise a future
reader will "fix" your test.

### THE RULE FOR THE TWO "run it and pin that" ROWS

For those two, and only those two: run the function, see what it returns, and write a test
asserting that. You are **recording** existing behaviour, not deciding what it should be.

If what it returns looks wrong to you — say so in the pull request. Do not change the function.

### STEPS

1. Read `qr.test.ts`.
2. Read `cn.ts` (4 lines) and `format.ts` (about 20).
3. Write `cn.test.ts` first — it is smaller.
4. Then `format.test.ts`.
5. Give each file an opening comment saying why the tests exist, in the style of the reference.

### DONE WHEN

```bash
cd frontend
pnpm --filter @opsgrids/ui test
pnpm --filter @opsgrids/ui typecheck
pnpm --filter @opsgrids/ui lint
```

### DO NOT TOUCH

`cn.ts`, `format.ts`, or any component. Do not add a dependency — no `jsdom`, no testing
library. These are pure functions; `vitest` alone is all they need.

---

# CARD D2 — test the CRM's money formatters

**BRANCH** `feat/d2-crm-format-tests`

**GOAL** Pin the six functions that turn stored values into what a salesperson reads on screen.

### WHY THIS MATTERS

Money on this platform is stored as an **integer number of minor units** — paise, not rupees.
`250000` means ₹2,500.00. This is one of the eleven decisions in `docs/adr/0000-phase-0-decisions.md`
that cannot be reversed. `formatMoney` is the function that turns that integer into the string a
person reads, and nothing currently tests it.

### REFERENCE

Your own card D1, and `frontend/opsgrids-ui/src/lib/qr.test.ts`.

### FILES YOU WILL TOUCH

```
frontend/product-crm/src/lib/format.test.ts    ← new file
```

### THE CONTRACT

`frontend/product-crm/src/lib/format.ts` exports six functions. Test all six.

- `formatMoney(minor, currency = "INR")` — at minimum: a whole amount, an amount with paise,
  zero, and a negative amount. Include one large value to check the grouping (Indian digit
  grouping is `12,34,567`, not `1,234,567` — check what the function actually does and pin it).
- `formatMoneyCompact(minor, currency = "INR")` — a small value, and values large enough to be
  abbreviated. Pin what it actually returns.
- `formatDate(iso)` — a normal date, and `null`.
- `formatDateTime(iso)` — a normal timestamp, and `null`.
- `formatRelative(iso, now)` — **always pass the `now` argument explicitly.** The file exports a
  fixed `NOW_ISO` constant for exactly this. A test that depends on the real clock passes today
  and fails on a random Tuesday. Cover: same day, one day ago, several days ago, a future date.
- `initials(name)` — the same cases as D1.

**For `null` inputs, run the function and pin what it returns.** Never assert that it renders
the word `"null"` — if it does, that is a defect: report it in the pull request and do not fix
it on this card.

### STEPS

1. Read `format.ts` — all 114 lines, including the comments.
2. Write one `describe` block per function, in the file's own order.
3. Run the suite as you go, not once at the end.

### DONE WHEN

```bash
cd frontend
pnpm --filter @opsgrids/product-crm test
pnpm --filter @opsgrids/product-crm typecheck
pnpm --filter @opsgrids/product-crm lint
```

### DO NOT TOUCH

`format.ts` itself. Any other app.

---

# CARD D3 — test the pipeline arithmetic

**BRANCH** `feat/d3-crm-derive-tests`

**GOAL** Pin the ten functions that compute every number on the CRM dashboard.

### REFERENCE

`frontend/product-crm/src/data/derive.ts` — read it fully first. Every function in it is pure:
deals in, numbers out, no network, no clock except where one is passed in.

### FILES YOU WILL TOUCH

```
frontend/product-crm/src/data/derive.test.ts    ← new file
```

### THE CONTRACT

Build **one small fixture array of deals** at the top of the test file — six or seven is plenty
— covering: at least one deal in each stage, at least one won, at least one lost, at least one
with no owner, and a spread of values. Every test uses that one array. Do not import the app's
real fixtures; a test that changes when the demo data changes is not a test.

Then one `describe` block per exported function:

`openDeals` · `pipelineValueMinor` · `weightedPipelineMinor` · `openCount` · `winRatePct` ·
`wonValueMinor` · `stageFunnel` · `dealsByStage` · `needsAttention` · `dealsOwnedBy`

For each, work out the expected answer **by hand from your fixture** and assert that number.
Do not call the function to find out what to expect — that tests nothing.

Two that need particular care:

- **`winRatePct`** — assert the empty case explicitly. A win rate over zero closed deals must
  not be `NaN` or a crash. Whatever it returns, pin it, and say in the pull request what it is.
- **`needsAttention(deals, today)`** — pass `today` explicitly, always. Cover a deal that is
  stale, one that is not, and the exact boundary between them.

### STEPS

1. Read all 121 lines of `derive.ts`.
2. Build the fixture array. Write out on paper what each function should return for it.
3. One `describe` per function, in the file's order.
4. If a function returns something you cannot explain from the code — stop and comment. That is
   either a bug worth reporting or a misunderstanding worth clearing up, and both are more
   valuable than a test that enshrines it.

### DONE WHEN

```bash
cd frontend
pnpm --filter @opsgrids/product-crm test
pnpm --filter @opsgrids/product-crm typecheck
pnpm --filter @opsgrids/product-crm lint
pnpm --filter @opsgrids/product-crm build
```

### DO NOT TOUCH

`derive.ts` itself.

---

# CARD D4 — the four-state audit

**BRANCH** `feat/d4-crm-states-audit`

**GOAL** Make every CRM screen behave when there is nothing to show, or something goes wrong.

### REFERENCE

`frontend/CLAUDE.md`, the section "States that are not the happy path". And
`frontend/product-crm/src/routes/Activities.tsx`, which already does this correctly.

### FILES YOU WILL TOUCH

Only files under `frontend/product-crm/src/routes/`. There are seven screens: `Overview`,
`Deals`, `DealDetail`, `Companies`, `CompanyDetail`, `Contacts`, `Activities`.

### THE CONTRACT

For each of the seven screens, check all four states and fix what is missing:

| State | Requirement |
|---|---|
| **loading** | present, and does not shift the layout when it resolves |
| **error** | says what went wrong **and what to do about it**, and offers a retry |
| **empty** | says what to do next, not just "No results" |
| **full** | the normal screen |

Put a table in the pull request body: one row per screen, one column per state, marked as
already-correct or fixed-by-this-PR. That table is the deliverable as much as the code is.

Use the existing components in `@opsgrids/ui`'s `States.tsx`. Do not write new ones, and do not
copy them into this app.

Error messages: no apologies, no vagueness. *"Could not load deals. Check your connection and
try again."* — not *"Oops! Something went wrong :("*.

### STEPS

1. Read the `frontend/CLAUDE.md` section, then `Activities.tsx`, then `States.tsx`.
2. Go screen by screen. Force each state by hand: stop the backend for the error state, and
   sign in as an organization with no data for the empty state (ask if you do not have one).
3. Fix as you go. Keep the pull request to this one concern — do not restyle anything.

### DONE WHEN

```bash
cd frontend
pnpm --filter @opsgrids/product-crm typecheck
pnpm --filter @opsgrids/product-crm lint
pnpm --filter @opsgrids/product-crm test
pnpm --filter @opsgrids/product-crm build
```

and the seven-row table is in the pull request.

---

# CARD D5 — the two URLs that go nowhere

**BRANCH** `feat/d5-crm-gateway-paths`

**GOAL** Fix two API calls that cannot succeed.

> **Do not start this card until the reviewer has answered the question below.** It is on the
> card deliberately: this is exactly the kind of question you must never decide yourself.

### THE PROBLEM

`frontend/product-crm/src/api/client.ts` calls two paths:

```
/companies
/owners
```

Neither exists. The gateway's route table (`services/app-gateway/routes.json`) offers
`/api/v1/customers` and `/api/v1/contacts`, both served by customer-service on port 8130. In
fixture mode nobody noticed, because the fixture branch never makes a request. In live mode
both calls fail.

### THE QUESTION FOR THE REVIEWER

There are two possible fixes and they are not equivalent:

1. **Change the app** to call `/customers` and `/contacts`, and map the response shapes.
2. **Change the gateway** to also route `/companies` and `/owners` to port 8130.

The reviewer decides. `routes.json` is a reviewer-owned file, so if the answer is (2), the
gateway half is not your change either — you will only do the app side.

### FILES YOU WILL TOUCH

`frontend/product-crm/src/api/client.ts`, and `src/api/types.ts` if the response shapes differ
from what the app expects. The card will be updated with the exact list once the question is
answered.

### DONE WHEN

```bash
cd frontend
pnpm --filter @opsgrids/product-crm typecheck
pnpm --filter @opsgrids/product-crm lint
pnpm --filter @opsgrids/product-crm test
pnpm --filter @opsgrids/product-crm build
```

and both calls return real data against a running gateway, evidenced in the pull request.

### DO NOT TOUCH

`services/app-gateway/routes.json`, whatever the answer turns out to be.

---

## After D5

`product-crm` still defaults to fixture data, unlike the six apps that already default to live —
flipping it is the next card, and D5 has to land first or every screen breaks at once. Beyond
that, `opsgrids-ui` has eighteen components and no tests that render any of them; adding the
tooling for that is a card for a reviewer, and the tests that follow it are cards for you.
