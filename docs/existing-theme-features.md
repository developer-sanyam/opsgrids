> **Historical.** This describes `frontend/opsgrids-website`, which was removed on 2026-09-19 when the marketing site moved to its own repository. Nothing it names still exists in this tree — see the amendment at the end of `docs/adr/0001-frontend-topology.md`.

# Existing theme features

An inventory of what the visual system actually provides today. For the rules on using and
extending it, see [theme-guide.md](theme-guide.md).

**Where it lives:** one file — `frontend/opsgrids-website/src/app/globals.css`, 258 lines. There is
no shared design-system package; `opsgrids-ui` is a declared workspace package with an empty
`src/`. So the honest description is *the marketing site has a theme*, not *the platform has a
design system*.

---

## Built

### Token system
A complete semantic palette in three declarations — light, dark by system preference, and dark by
explicit choice — covering surfaces (`--bg`, `--panel`, `--panel-2`, `--line`), four text weights
(`--ink`, `--ink-2`, `--mut`, `--faint`), accent (`--accent`, `--accent-ink`, `--accent-wash`),
two status pairs (`--ok`/`--ok-w`, `--warn`/`--warn-w`) and shape (`--r`, `--maxw`, `--shadow`).

**No colour is set outside a token anywhere in the stylesheet.** That discipline is the most
valuable thing the current theme has, because it is what makes the eventual extraction into a
design system mechanical instead of archaeological.

### Dark mode, done properly
Both entry points work: system preference *and* an explicit `data-theme` attribute, with the
`:not([data-theme='light'])` guard so an explicit light choice beats a system dark preference.
The shadow token is redefined for dark rather than reused — a dark-mode shadow that works is a
different colour, not a dimmer one.

### System font stack
`ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`. No webfont, so no FOUT
and no font payload.

### Component classes
Roughly 30, all marketing-shaped:

| Group | Classes |
|---|---|
| Layout | `.band`, `.bandhead`, `.grid`, `.three`, `.tw` |
| Hero and headings | `.hero`, `.mega`, `.lede`, `.eyebrow`, `.phead` |
| Cards | `.card`, `.cardhead`, `.cat`, `.catgroup` |
| Product content | `.features`, `.problem`, `.tagline`, `.connects`, `.status` |
| Pricing | `.plan`, `.plans`, `.total` |
| Actions | `.btn`, `.cta`, `.menu` |
| Utility | `.fine`, `.skip` |

### Accessibility basics
A `.skip` skip-to-content link, and a `.status` class used to render product availability
(`available` / `beta` / `soon`) as something other than colour alone — status that is only a
colour is invisible to a colour-blind reader.

---

## Absent

Everything an application needs and a marketing site does not:

| Missing | Why it matters |
|---|---|
| **Shared package** | Tokens cannot be consumed by any other app; a second app copies them and drifts |
| **Form controls** | No input, select, checkbox, radio, textarea, or validation styling |
| **Data display** | No table, no dense list — the single biggest gap for an admin product |
| **Feedback** | No toast, banner, inline error, or confirmation |
| **Overlays** | No modal, drawer, popover, tooltip |
| **Navigation** | No sidebar, breadcrumb, tab set, pagination control |
| **Empty and loading states** | No skeleton, spinner, or empty-state pattern |
| **Icon set** | None chosen |
| **Motion tokens** | No duration or easing tokens; transitions are ad hoc |
| **Density** | One density. Admin tables generally want a compact option |
| **Theme toggle UI** | `data-theme` is respected; nothing sets it |
| **Component workshop** | No Storybook or equivalent, so no place to review states in isolation |
| **Visual regression baselines** | Gate row 12 has nothing to run |
| **Token lint** | Gate row 18 has nothing to run; the "no raw values" rule is unenforced |

---

## The standards exist even though the components do not

Everything in the Absent table now has a written rule in
[theme-guide.md](theme-guide.md), adopted from the digital-accounts web guide, which had already
solved the data-dense problem this platform is about to hit:

| Gap | The rule that now governs it |
|---|---|
| Data display | Sticky headers, zebra via `--panel-2`, right-aligned **tabular numerals**, colour on the value rather than the row, own scroll container |
| Money | Render from integer minor units; format with `Intl.NumberFormat('en-IN')` — lakh/crore, not thousands |
| Density | A comfortable/compact toggle on dense screens, switched at token level |
| Motion | One orchestrated page-load reveal per screen; respect `prefers-reduced-motion` |
| Empty and loading states | Required for anything that displays data, designed rather than defaulted |
| Responsive | Container queries so a component adapts to its pane; tables collapse to cards on mobile |
| Token discipline | No raw values **and no inline styles**, with one narrow exception |

Two of these are worth knowing before the first product screen is written, because retrofitting
them is expensive:

- **Tabular numerals.** Proportional digits do not align in a column, so every payslip, invoice
  and usage total is harder to scan. It is one CSS property and it has to be there from the
  first table.
- **Indian number grouping.** ₹12,34,567.89, not ₹1,234,567.89. Immediately obvious to every
  Indian customer and invisible to everyone else — which is exactly why it ships broken.

The design tokens for money direction — `--credit`, `--debit`, `--pending` — are **specified and
not yet defined**, since nothing renders money yet.

## What this means in practice

The theme is **good for what it covers and covers very little of an application**. The marketing
site is five pages of static content; the first product screen will need forms, tables, empty
states and overlays on day one, none of which exist.

Two consequences worth planning around:

1. **Do not treat the current file as the design system.** Marketing pages and application
   screens want different components. Only the *tokens* are obviously shared, which is why they
   are semantic and why they should keep their names through the move.
2. **The workshop matters more than the components.** Building components without somewhere to
   review every state — hover, focus, disabled, loading, error, empty, long content, dark mode —
   is how a system accumulates variants nobody can see. That is what `design-system-architect`
   and gate rows 12 and 18 are for, and none of it exists yet.
