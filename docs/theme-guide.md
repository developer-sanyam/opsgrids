> **Historical — superseded by [`frontend/CLAUDE.md`](../frontend/CLAUDE.md) (design and code rules) and the `frontend/opsgrids-ui` package (tokens in `src/styles/console.css`, components in `src/components/`).** This page describes a theme in the marketing site's `globals.css`, which was removed on 2026-09-19. `opsgrids-ui` is no longer empty. Nothing below is current.

# Theme guide

How to use and extend the visual system. What exists today is in
[existing-theme-features.md](existing-theme-features.md); this page is the working rules.

**Scope, honestly:** the theme currently lives in one file — the marketing site's
`globals.css`. There is no shared design-system package yet (`opsgrids-ui` is declared and empty),
so "the theme" is one stylesheet that product apps are expected to inherit from when they exist.
Everything below is written so that inheritance is possible later rather than a rewrite.

Several conventions here are adopted from the digital-accounts web guide, which had already
solved the data-dense problem this platform is about to hit. Where a rule came from there it is
marked **[adopted]**.

---

## Two rules

### 1. Nothing sets a colour, radius or shadow outside a token

Not one hex value in a component, ever.

This is not tidiness. Light and dark are both defined at token level, so a raw hex is a value
that cannot follow the theme — it looks correct in whichever mode you built it in and wrong in
the other, and nobody notices until a customer does.

```css
/* no */  .card { background: #FFFFFF; border: 1px solid #E2E6EB; }
/* yes */ .card { background: var(--panel); border: 1px solid var(--line); }
```

### 2. No inline styles **[adopted]**

Never `style="…"` or `style={{…}}` for a visual property. Style with classes that read tokens.

**The single permitted exception** is setting a CSS custom property that a class then consumes:

```tsx
/* fine — the class decides what the value means */
<div className="bar" style={{ '--bar-width': `${pct}%` }} />

/* not fine */
<div style={{ width: `${pct}%`, background: '#1E5EFF' }} />
```

Inline styles beat every stylesheet rule, cannot be themed, cannot be overridden by a density or
dark-mode variant, and are invisible to the token lint. One is enough to break a component in
dark mode permanently.

Gate row 18 (token lint) enforces both rules and is **not yet wired**.

---

## Tokens

Semantic names, never literal ones — `--panel`, not `--white`. A literal name is a lie in dark
mode.

### Surfaces, lines, text
| Token | Use |
|---|---|
| `--bg` | Page background |
| `--panel` | Raised surface — cards, menus |
| `--panel-2` | Recessed or secondary fill |
| `--line` | Borders and dividers |
| `--ink` / `--ink-2` / `--mut` / `--faint` | Text, primary → faintest |

Four text weights, deliberately. If you need a fifth, the hierarchy is probably wrong.

### Accent and status
`--accent`, `--accent-ink`, `--accent-wash`; `--ok`/`--ok-w`, `--warn`/`--warn-w`. The `-w`
suffix is always the wash — a tint the matching foreground is legible against **in both modes**.
They are designed as pairs; do not mix `--ok` with `--warn-w`.

### Money direction — **[adopted]**, and not yet defined

The platform bills, runs payroll and issues invoices. It needs money-direction tokens, and they
must be **accessible variants rather than pure red and green**:

```css
--credit;   /* money in  */
--debit;    /* money out */
--pending;  /* not yet settled */
```

To be added with the design system. Colour is the *secondary* signal — see accessibility below.

### Shape
`--r` (radius), `--maxw` (content width), `--shadow`. The shadow is redefined in dark rather than
dimmed: a dark-mode shadow that works is a different colour, not a fainter one.

---

## Light and dark

Three declarations, all three needed:

```css
:root { /* light — the complete palette */ }

@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) { /* dark, by system preference */ }
}

:root[data-theme='dark'] { /* dark, by explicit choice */ }
```

The `:not([data-theme='light'])` guard lets an explicit light choice beat a system dark
preference. Dropping it makes the toggle one-directional — a bug visible only to users whose OS
is in dark mode, which is why it survives review so easily.

**Adding a token means adding it in all three places.** A token defined only in `:root` silently
keeps its light value in dark mode. That is the most common way to break this system.

---

## Type

The marketing site uses a system stack, which is defensible there — no webfont, no FOUT, no
payload.

**For product UI that is not sufficient**, and two rules apply:

**Avoid the defaults.** Inter, Roboto, Arial and bare `system-ui` are the fastest route to
generic. Pick a display face with character and a workhorse for UI text, and load them with a
real fallback stack behind each. **[adopted]**

**Numerals must be tabular. Non-negotiable.** **[adopted]**

```css
.amount { font-variant-numeric: tabular-nums; text-align: right; }
```

Proportional digits have different widths, so a column of amounts fails to align and the eye
cannot scan it. Every payslip, invoice line, ledger row and usage total in this platform depends
on this one property.

### Formatting money

Two rules that belong together:

1. **Render from integer minor units.** `platform_core.money` stores minor units with an ISO
   currency; the frontend must not convert to a float on the way in. Floats reintroduce exactly
   the rounding errors the backend representation exists to prevent.
2. **Format with `Intl.NumberFormat('en-IN')`** for INR. **[adopted]** Indian grouping is
   lakh/crore — ₹12,34,567.89, not ₹1,234,567.89. Getting this wrong is immediately obvious to
   every Indian customer and invisible to everyone else.

---

## Spacing and density

A 4px grid; multiples of 4, and 8 for anything structural. Consistent spacing does more for
perceived quality than any individual component.

**Offer a density toggle on data-dense screens** — comfortable and compact. **[adopted]** Someone
reviewing a day of attendance exceptions or a payroll run wants more rows per screen than
someone glancing at a dashboard. Density is a token-level switch, not a separate component set.

---

## Data-dense tables

The single biggest gap between what the theme has and what the products need, so the rules are
here before the components exist. **[adopted, wholesale]**

- **Sticky headers** (`position: sticky`) — a header that scrolls away makes a long table
  unreadable.
- **Zebra striping** via `--panel-2`, not a new colour.
- **Right-align numbers**, left-align text, and use tabular numerals.
- **Colour the value, not the row.** A whole row tinted red reads as an error; the delta tinted
  `--debit` reads as money out. Row colour is for selection and hover.
- **Wide tables scroll inside their own container** with `overflow-x: auto`. The page body must
  never scroll horizontally.
- **Every table needs an empty state and a loading state**, designed rather than defaulted.

---

## Motion

**One orchestrated page-load reveal per screen** beats scattered micro-interactions.
**[adopted]** A short staggered fade of summary cards then the table — via `animation-delay`,
CSS-only where possible — creates more of an impression than a dozen hover effects.

Subtle row hover; a brief flash on a value that changed. Nothing that moves on every keystroke.

**Always respect `prefers-reduced-motion`.** For some users motion is not decoration, it is
nausea.

---

## Depth and background

Layer surfaces — `--bg` → `--panel` → `--panel-2` — rather than defaulting to flat white.
**[adopted]** A very subtle texture or faint gradient behind an empty state adds atmosphere;
anything you notice as a pattern is noise.

---

## Responsive

- **Container queries, not just viewport queries.** **[adopted]** A component that lives in a
  side pane should adapt to the pane, not to the window.
- **Tables collapse to stacked cards on mobile.** A horizontally scrolling table on a phone is a
  table nobody reads.
- Desktop can go multi-pane — list beside detail — where the task is review-and-drill.

---

## States and accessibility

Every interactive element needs **hover, focus-visible, active and disabled**. Use
`:focus-visible`; never remove an outline without replacing it with something at least as
visible. A keyboard user who cannot see focus cannot use the page.

**Never encode meaning in colour alone.** **[adopted]** Pair credit/debit with a sign or an
icon, and product status with a label. Roughly one in twelve men has some colour-vision
deficiency, and a red number that reads as "just a number" is a real support call.

WCAG 2.2 AA contrast on every text and number pair, **in both modes**. There is a `.skip`
skip-to-content link — keep it, and keep it first in the DOM.

---

## Adding a component

1. Check whether an existing class already does it — around 30 exist, listed in
   [existing-theme-features.md](existing-theme-features.md).
2. Compose from tokens only. No inline styles.
3. Define all four interaction states, plus empty and loading if it displays data.
4. Verify in **both** modes. Most theme bugs are "looks fine in the mode I built it in".
5. Check contrast in both modes.

## When the design system arrives

The marketing stylesheet is the prototype, not the destination. When `opsgrids-ui` is built, tokens
move there and the site imports them; token names should survive the move, which is why they are
semantic.

**Do not extract this file wholesale.** Marketing pages and application screens want genuinely
different components — the site has no table, no form control and no empty state. Only the
*tokens* are obviously shared. The rules on this page, by contrast, all transfer.
