# Theme guide

How to use and extend the visual system. What exists today is documented in
[existing-theme-features.md](existing-theme-features.md); this page is the working rules.

**Scope, honestly:** the theme currently lives in one file — the marketing site's
`globals.css`. There is no shared design-system package yet (`atmora-ui` is declared and empty),
so "the theme" is one stylesheet that the product apps are expected to inherit from when they
exist. Everything below is written so that inheritance is possible later rather than a rewrite.

---

## The one rule

**Nothing sets a colour, a radius or a shadow outside a token.** Not one hex value in a
component, ever.

This is not tidiness. Light and dark are both defined at token level, so a raw hex in a component
is a value that cannot follow the theme — it looks correct in whichever mode you built it in and
wrong in the other, and nobody notices until a customer does.

```css
/* no */
.card { background: #FFFFFF; border: 1px solid #E2E6EB; }

/* yes */
.card { background: var(--panel); border: 1px solid var(--line); }
```

The eventual token lint (gate row 18) enforces exactly this and is not yet wired.

## The tokens

Semantic names, not literal ones — `--panel`, not `--white`. A literal name is a lie in dark
mode.

### Surfaces and lines
| Token | Use |
|---|---|
| `--bg` | Page background |
| `--panel` | Raised surface — cards, menus |
| `--panel-2` | Recessed or secondary fill |
| `--line` | Borders and dividers |

### Text
| Token | Use |
|---|---|
| `--ink` | Primary text |
| `--ink-2` | Secondary — body copy against a panel |
| `--mut` | Muted — captions, metadata |
| `--faint` | Faintest — disabled, watermarks |

Four weights, deliberately. If you need a fifth, the hierarchy is probably wrong.

### Accent and status
| Token | Use |
|---|---|
| `--accent` | Primary action, links |
| `--accent-ink` | Accent text on a wash, or hover |
| `--accent-wash` | Tinted background behind accent content |
| `--ok` / `--ok-w` | Success foreground / wash |
| `--warn` / `--warn-w` | Warning foreground / wash |

The `-w` suffix is always the wash — a background tint that the matching foreground is legible
against **in both modes**. They are designed as a pair; do not mix `--ok` with `--warn-w`.

### Shape
| Token | Use |
|---|---|
| `--r` | Corner radius (`10px`) |
| `--maxw` | Content max width (`1120px`) |
| `--shadow` | The elevation shadow — different in dark, deliberately |

## Light and dark

Three declarations, and all three are needed:

```css
:root { /* light — the complete palette */ }

@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) { /* dark, for system preference */ }
}

:root[data-theme='dark'] { /* dark, for an explicit choice */ }
```

The `:not([data-theme='light'])` guard is what lets an explicit light choice win over a system
preference for dark. Dropping it makes the toggle one-directional — a bug that only appears for
users whose OS is in dark mode, which is why it survives review easily.

**When adding a token, add it in all three places.** A token defined only in `:root` silently
keeps its light value in dark mode. That is the most common way to break this system.

## Type

The stack is a system stack — `ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto,
sans-serif`. No webfont is loaded, so there is no flash of unstyled text and no font payload.

If a brand face is introduced later it belongs in the design-system package with a real fallback
stack behind it, not in a component.

## Spacing

A 4px grid. Use multiples of 4 and prefer 8 for anything structural. Consistent spacing does more
for perceived quality than any individual component.

## States

Every interactive element needs **hover, focus-visible, active and disabled**. Focus in
particular: use `:focus-visible`, never remove the outline without replacing it with something
at least as visible. A keyboard user who cannot see focus cannot use the page.

There is a `.skip` link class for skip-to-content — keep it, and keep it first in the DOM.

## Adding a component

1. Check whether an existing class already does it — there are about 30, listed in
   [existing-theme-features.md](existing-theme-features.md).
2. Compose from tokens only.
3. Define all four interaction states.
4. Verify in **both** modes before opening a PR. Most theme bugs are "looks fine in the mode I
   built it in".
5. Check contrast, both modes. WCAG 2.2 AA is the bar the accessibility auditor holds.

## When the design system arrives

The marketing site's stylesheet is the prototype, not the destination. When `atmora-ui` is built:

- Tokens move there and the site imports them.
- Token names should survive the move — that is why they are semantic.
- Component classes here are candidates, not commitments; the product apps have different needs
  (dense tables, forms, empty states) that a marketing site never exercises.

Do not build the design system by extracting this file wholesale. Marketing pages and application
screens want genuinely different components; only the **tokens** are obviously shared.
