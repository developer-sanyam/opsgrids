# @opsgrids/product-storefront

The themed B2B storefront: a merchant activates it, adopts a theme, edits their store, and dealers
buy from the result.

```bash
pnpm dev        # http://localhost:4400
pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

## Environment

Two variables, neither with a secret in it. There is no `.env.example` here because no app in this
workspace has one; this section is the reference.

| Variable | Default | What it does |
|---|---|---|
| `VITE_USE_FIXTURES` | fixtures **on** in dev | `false` runs against the real gateway. Fixture mode is dev-only and refuses uploads with an explanation rather than faking one. |
| `VITE_ASSET_BASE_URL` | empty (same origin) | Where uploaded images are served from. Locally that is MinIO: `http://localhost:9100/opsgrids-public`. |

**`VITE_ASSET_BASE_URL` has no dev default on purpose.** A `localhost` default compiled into a
production bundle would point every merchant's imagery at a machine that is not there — a failure
that shows up only after deploy, on someone else's store. Empty means same-origin, which is wrong
loudly rather than quietly.

It must agree with the document service's own `PUBLIC_ASSET_BASE_URL` (see `.env.example` at the
repo root). The two live in different files under different names and **nothing checks that they
match** — a unit test pins the URL *shape* both sides agree on, not the configured values. If
uploaded images 404 locally, this is the first thing to check.

## How it is put together

| Path | Holds |
|---|---|
| `src/theme/schema.ts` | **The contract.** `StoreDoc`, `PageDoc`, `SectionInstance`, `ThemeTokens`, and the grid constants. Both the renderer and the editor code against these names. |
| `src/theme/sections.ts` | The section library — every block a merchant can place, and every setting it exposes. The editor builds its palette and its settings panel from this. |
| `src/theme/render/` | One component per section type, plus the page and store renderers. Nothing here reads anything but its props. |
| `src/theme/themes/` | Six shipped themes, each a full set of composed pages. |
| `src/api/` | The client and the fixtures behind it. |

## Three things worth knowing before you change anything

**The canvas is a 12-column grid.** `layout.x` is 0–11, `w` is 1–12, and `y`/`h` are row units of
40px. Below `STACK_WIDTH` (768px of *canvas*, not viewport) every section goes full width and the
page stacks by ascending `y`. Free pixel positioning is deliberately not the model.

**Adopting a theme forks it.** A `StoreDoc` is a deep copy of a theme's tokens and pages; after
that there is no link back. Improving a theme cannot reach a store that already adopted it, and
re-adopting replaces the pages wholesale. See the comment on `adoptTheme`.

**There is no storefront service yet.** Everything renders from `src/api/fixtures.ts` and the theme
library. `USE_FIXTURES` defaults to on in dev; the live paths are written out in `client.ts` so
standing the service up is a flag flip.

## Two design languages, on purpose

The merchant's chrome — sidebar, page headers, buttons, states — is `@opsgrids/ui`, on Console
tokens. Everything under `src/theme/render/` is the *buyer's* surface, whose palette, type and
radius are the merchant's data; it styles itself only from `var(--st-*)` custom properties set by
`ThemeFrame`. A Console component inside a store page would be violet in every theme, which is the
opposite of what a theme is for, so the two sets never mix.
