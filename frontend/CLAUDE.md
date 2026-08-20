# CLAUDE.md — Atmora frontend

Read [`../docs/adr/0001-frontend-topology.md`](../docs/adr/0001-frontend-topology.md) before
adding an app or moving code between them.

You are building production interfaces, not demos. The standard below is not preference —
it is what separates an interface people trust with their payroll from one that looks like
a prototype.

## Layout

```
atmora-website      Marketing — public, SEO-critical, mostly static   Next.js
atmora-dashboard    The ecosystem home after sign-in                  Vite · React
atmora-admin        Customer administration                           Vite · React
atmora-internal     Atmora staff console                              Vite · React
product-*           One standalone app per product                    Vite · React
atmora-ui           Design system — the components every app imports
atmora-client       Auth + API SDK, entitlement and permission hooks
atmora-config       Shared tsconfig / eslint / vite presets
```

**An app may not fork a component into itself.** If a product needs a variant, it goes into
`atmora-ui` as a variant. Nine apps growing nine date pickers is the failure this rule
exists to prevent.

## Design rules

**No AI-generated house style.** Specifically avoid: glowing gradients, floating cards with
no purpose, purple-to-indigo accents, everything centred, `rounded-lg` on every surface,
emoji as section markers, and hero sections that say nothing. If a choice would look at
home in any generic template, it is the wrong choice here.

**Restrained palette, high contrast, deliberate hierarchy.** One accent, used sparingly for
the single most important action on a screen. Semantic colour (success / warning / danger)
is separate from the accent and never doubles as decoration. Every foreground/background
pair meets WCAG AA — 4.5:1 for body text, 3:1 for large text and meaningful UI edges.

**Spacing is a 4px grid.** Use `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64`. Nothing in between,
and nothing arbitrary. Group related things with proximity before reaching for a border or
a card — whitespace is the cheapest hierarchy there is.

**Typography.** A real type scale, not ad-hoc sizes. Body copy near 65 characters per line.
`text-wrap: balance` on headings. `font-variant-numeric: tabular-nums` anywhere digits line
up in a column, which in this platform is most tables.

**Every interactive element has five states**, and none is an afterthought: rest, hover,
focus-visible, active, disabled. Anything that hits the network has a sixth: loading.
`:focus-visible` gets a real ring — removing focus outlines makes the product unusable by
keyboard, which includes people using screen readers and anyone whose mouse just died.

**States that are not the happy path.** Every list has an empty state that says what to do
next, an error state that says what went wrong and how to fix it, and a loading state that
does not shift the layout when it resolves. A screen that only has a full state is half a
screen.

## Code rules

- **Semantic HTML first.** A `<button>` for an action, an `<a>` for navigation, a real
  `<table>` for tabular data, `<label>` tied to every control. Reach for ARIA only when
  markup genuinely cannot express it — a `div` with `role="button"` and a keydown handler
  is a bug that took effort.
- **Accessibility is not a pass at the end.** Keyboard reachable, focus order matching
  visual order, `aria-live` for anything that changes without a click, and state never
  conveyed by colour alone (a red dot needs a label too).
- **TypeScript strict.** `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` are on.
  No `any`, no non-null `!` to silence the compiler — if the type is wrong, fix the type.
- **Zero placeholder or dead code.** No `TODO` components, no commented-out blocks, no
  lorem ipsum, no unused props "for later". Delete it; git remembers.
- **Modular and small.** A component does one thing. When it needs a paragraph to explain,
  it is two components.
- **Respect `prefers-reduced-motion`.** Motion is for orientation — what moved, where it
  went — never decoration.

## Data and auth

- The access token lives **in memory only**. Never `localStorage`, which any XSS payload can
  read. The refresh token is an httpOnly cookie the JavaScript never sees.
- `useEntitlement(feature)` and `<RequirePermission>` **hide UI**. They are not enforcement —
  the server refuses the call regardless. Never let a check here be the only one.
- Errors arrive as RFC 7807 `problem+json`. Parse them into typed errors and map
  field-level detail onto the form control that caused it; a form that reports "something
  went wrong" at the top when it knows which field is wrong is failing the user on purpose.
- Never retry a 4xx. An authorization failure does not fix itself, and retrying makes the
  user wait to be told no.

## Before you call it done

```bash
pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

Then actually use it: tab through the whole screen, resize to 360px wide, and look at it in
both light and dark. A screenshot is not verification — running it is.
