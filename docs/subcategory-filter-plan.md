# Subcategory filter — plan

A design proposal for filtering the product catalogue. **Nothing here is built.**

---

## Today

`frontend/atmora-website/src/content/products.ts` is the single source of truth for the marketing
site — the index, every product page, the pricing table and the sitemap are all generated from
it. Adding a product is an entry in that file, not a new page.

Each product carries a **single-level** `category`:

```ts
category: 'People' | 'Revenue' | 'Work' | 'Operations' | 'Finance'
```

`/products` groups by category into static sections. There is **no interactive filter**, no
subcategory, and no client-side state on the page at all.

## The problem

With nine products, static grouping is fine — arguably better than a filter, because the whole
catalogue fits on one screen and grouping communicates the shape of the ecosystem.

It stops being fine at roughly 20+, or as soon as products differentiate *within* a category.
"Work" already holds both PulseBoard and a standalone project-management product; "People" will
hold HRMS, recruiting and payroll, which are different purchases for different buyers.

**The real driver is not volume, it is the buyer.** An HR manager and a finance controller both
land on `/products` and neither should have to read past the other's products.

## Proposed shape

### 1. Add subcategory to the type

```ts
export interface Product {
  category: 'People' | 'Revenue' | 'Work' | 'Operations' | 'Finance';
  /** Optional second level. Absent means the product sits directly under its category. */
  subcategory?: string;
}
```

**Optional, and a plain string rather than a union per category.** A union is the more "correct"
type and it makes adding a product a type-level puzzle rather than an edit — which is exactly the
friction the catalogue file was designed to avoid. Validate the set in a test instead: assert
every subcategory appears at least twice, so a typo produces a lonely group and a red test rather
than a silent orphan.

### 2. Filter state lives in the URL

`?category=People&sub=Payroll`, not React state.

This is the load-bearing decision. It makes a filtered view **linkable and shareable** — a
salesperson sends a prospect exactly the two products they care about — and it keeps the page
server-rendered, which a marketing page cannot give up. Filter state held only in a component is
invisible to search engines and cannot be linked.

Read via `useSearchParams`; navigate with `router.replace` and `scroll: false` so the page does
not jump on each interaction.

### 3. Progressive enhancement, not a client-side rewrite

- Render **all** products server-side, grouped as today.
- Filters are links (`<a href="?category=People">`), so they work with JavaScript disabled and
  are crawlable.
- Enhance with client-side hiding for instant feedback once hydrated.

The `/products` page stays a server component; only the filter bar is a client component. Turning
the whole page client-side to add a filter would trade the site's SEO for an interaction that
does not need it.

### 4. Interface

A filter bar above the grid: category chips, and subcategory chips that appear only when a
category is selected. Each chip carries its result count — a filter that leads to an empty page
is a dead end, and the count prevents it.

Always render a **"clear filters"** affordance when any filter is active, and a real empty state
if a combination genuinely matches nothing.

### 5. What not to build

- **No search box.** Nine products do not need one, and a search that returns nothing on a typo
  is worse than a list.
- **No multi-select within a category.** Doubles the state space and the URL grammar for a case
  nobody has asked for.
- **No filtering by plan or price.** Pricing is per product with tiers; filtering on it implies a
  comparison the pricing page should own.
- **No animation on filter.** Reordering cards with motion reads as a glitch when it happens on
  every chip press.

## Acceptance

- A filtered URL, pasted fresh, renders the filtered view server-side.
- Filters work with JavaScript disabled.
- Every chip shows a count; no chip leads to an empty result.
- Clearing filters returns to the full grouped view.
- Keyboard reachable, focus visible, and the result count announced to screen readers when it
  changes.
- No raw colour values — chips use theme tokens ([theme-guide.md](theme-guide.md)).

## Cost and sequencing

Small — roughly a day, mostly the filter bar and its states.

**It is not on the critical path.** The catalogue has nine products, all fitting on one screen,
and the platform has no product UI at all. This is a marketing-site refinement competing against
row-level security and the end-to-end suite ([backlog.md](backlog.md)).

Worth doing when the catalogue passes ~15 products, or when a second product lands in a
subcategory that makes the current grouping actively misleading — whichever comes first.
