# CLAUDE.md — OpsGrids frontend

Read [`../docs/adr/0001-frontend-topology.md`](../docs/adr/0001-frontend-topology.md) before
adding an app or moving code between them.

You are building production interfaces, not demos. The standard below is not preference —
it is what separates an interface people trust with their payroll from one that looks like
a prototype.

## Layout

```
opsgrids-dashboard    The ecosystem home after sign-in                  Vite · React
opsgrids-internal     OpsGrids staff console                              Vite · React
product-*           One standalone app per product                    Vite · React
opsgrids-ui           Design system — the components every app imports
opsgrids-client       Auth + API SDK, entitlement and permission hooks
opsgrids-config       Shared tsconfig / eslint / vite presets
```

**An app may not fork a component into itself.** If a product needs a variant, it goes into
`opsgrids-ui` as a variant. Nine apps growing nine date pickers is the failure this rule
exists to prevent.

## Design rules

*Updated 2026-09-25 to the owner's minimalist dashboard direction (the UI modernization
brief and its mockups). Where this section once banned indigo, rounded cards and emoji, the
owner ruled that the mockups win; the rules below replace those bans.*

**Minimal, light-first, spacious.** White or very light neutral ground, generous spacing,
subtle borders, very subtle shadows, a small number of colours. It should read as a modern
SaaS product, not a traditional ERP. Avoid glowing gradients, decorative illustration and
hero sections that say nothing. If an element does not help someone decide or act, remove it.

**Cards.** Rounded cards are the standard surface: 12px radius (`rounded-card`), a hairline
edge (`shadow-card` draws it), no heavy shadow. Controls use 8px, badges 6px. Do not nest
cards; group with proximity first.

**One indigo accent** (`accent`), used for the primary action and the current location.
Semantic colour (success / warning / danger / info) is separate from the accent and never
doubles as decoration. Every foreground/background pair meets WCAG AA — 4.5:1 for body
text, 3:1 for large text and meaningful UI edges (input borders use `field-edge`).
`opsgrids-ui/src/styles/tokens.test.ts` checks every defined pair in light and dark.

**Emoji** appear only in the home greeting ("Good morning, Sanyam 👋"). Never as section
markers or status icons.

**Buttons have a hierarchy.** Primary for create, save, submit, continue, approve. Secondary
for cancel, back, filter, export. Destructive only for delete, suspend, remove and
irreversible cancels. Not every action is primary.

**Required fields carry a red asterisk** (`required` on `FormField`, which also sets the
accessible name). Optional fields are unmarked.

**Statuses use `StatusBadge`** and the shared status registry in `opsgrids-ui`. The same
status word always has the same colour in every app; do not write a local pill.

**Tokens, not values.** Colour, spacing, radius, shadow and type come from `console.css`.
The `opsgrids/tokens` lint rule rejects raw colours, arbitrary values and off-scale spacing
in feature code. Existing offenders are ratcheted down in `opsgrids-config/token-baseline.json`;
new code is held to zero. Do not raise a baseline count.

**Spacing is a 4px grid.** Use `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64`. Nothing in between,
and nothing arbitrary. Group related things with proximity before reaching for a border or
a card — whitespace is the cheapest hierarchy there is.

**Typography.** A real type scale, not ad-hoc sizes: page 28, section 18, card title 15,
body 14/13, caption 12, KPI 28/22. Weight tops out at 700. Body copy near 65 characters per
line. `text-wrap: balance` on headings. `font-variant-numeric: tabular-nums` anywhere digits
line up in a column, which in this platform is most tables.

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
