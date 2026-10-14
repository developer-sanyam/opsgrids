# Visual and accessibility testing

The UI modernization changes how every screen in 13 apps looks, mostly through design tokens
that a single edit carries everywhere. Before any of that lands, two tools record how the
interface looks today:

- **The workshop**: Storybook for `@opsgrids/ui`. Every exported primitive is rendered in every
  state worth seeing, from props alone, in light and dark.
- **The harness** (`opsgrids-visual`). Playwright takes a screenshot of every story, plus six real
  app screens rendered against contract-shaped API mocks. It does this in both themes at 375, 768
  and 1280 px (the brief's QA widths, §7) and runs axe on each rendering.

A change either leaves the pixels alone, or someone looks at the difference and accepts it by
committing new baselines. Accessibility can only get better: the check fails on any new axe
violation, and the violations that exist today are recorded as the starting point.

## Commands

Run these from `frontend/`, with Node 22 and pnpm 11 on PATH.

| What | Command |
|---|---|
| One-time: the pinned Chromium | `pnpm --filter @opsgrids/visual run visual:browsers` |
| Open the workshop (http://localhost:6006) | `pnpm --filter @opsgrids/ui storybook` |
| Build the workshop | `pnpm --filter @opsgrids/ui build-storybook` |
| **Check** screenshots and axe against the baselines | `pnpm visual:check` |
| **Update** baselines after reviewing a change | `pnpm visual:update` |
| Open the last run's report (expected / actual / diff) | `pnpm visual:report` |
| The gate row (installs Chromium, then checks) | `make ui-visual` (part of `make gate-full`) |
| The harness's own unit tests (fixture contracts, a11y arithmetic) | `pnpm --filter @opsgrids/visual test` (also in `pnpm -r test`) |

`visual:check` and `visual:update` first build the workshop and the three apps under test
(`opsgrids-dashboard`, `product-crm`, `opsgrids-internal`). Then they serve those builds on ports
4910–4913 and run Playwright. A full run takes about 4 minutes on 8 workers. To run fewer
workers, set `VISUAL_WORKERS` to a percentage (`50%`); a bare number is rejected by Playwright's config check. To run beside another checkout's harness (a second worktree),
move the four ports with `VISUAL_PORT_BASE=4920`; a run never reuses a server it did not start.

To work on one thing, run Playwright directly after one `visual:check` has built the targets:

```bash
cd frontend/opsgrids-visual
pnpm exec playwright test --grep actions-button          # one story file
pnpm exec playwright test specs/pages.spec.ts            # the app pages only
VISUAL_UPDATE=1 pnpm exec playwright test --grep actions-button --update-snapshots=changed
```

A filtered update rewrites only what it ran, and that includes the a11y baseline.

## What is covered

| Surface | Count | Renderings (× 2 themes × 3 widths) |
|---|---|---|
| Workshop stories (37 story files covering all 41 exported components and all ~108 icons) | 159 | 954 |
| App pages | 6 | 36 |
| **Total** | | **990 screenshots, 990 axe runs** |

The six app pages:

- `page/dashboard/marketplace`: the ecosystem home at `app.`
- `page/crm/overview`: a product dashboard
- `page/crm/deals-list`: Deals in list view, a `DataTable` with pagination
- `page/crm/new-deal-dialog`: the New deal `FormDialog`, open over the board
- `page/internal/overview`: the operator console's Overview
- `page/internal/accounts`: the operator console's Accounts

Baselines live in `opsgrids-visual/baselines/<platform>/<surface>/<theme>-<width>.png`. That is
990 PNGs, 18.7 MiB (stories 13.5 MiB, pages 5.2 MiB). Accessibility baselines live in
`opsgrids-visual/a11y-baseline.json`.

**A new story is covered automatically.** The harness reads the story list from the workshop
build's `index.json`. A new story has no baseline, so `visual:check` fails until someone runs
`visual:update` and commits the PNGs they looked at. `opsgrids-ui/src/stories.test.ts` fails if
an exported component has no story at all.

## Reviewing a diff

1. Run `pnpm visual:check`. Failing renderings are listed by story or page, theme and width.
2. Run `pnpm visual:report`. Each failure shows expected, actual and a diff, with a slider. The
   axe violations for every rendering are attached as JSON.
3. If the change is intended, run `pnpm visual:update` and commit the changed PNGs and
   `a11y-baseline.json` in the same PR as the code change. GitHub shows PNG diffs side by side
   ("2-up", "swipe", "onion skin"). A reviewer approves the pixels along with the code.
4. If it is not intended, fix the code. Never update baselines to make a red run green without
   looking at them.

`visual:update` only rewrites a PNG whose comparison failed (`--update-snapshots=changed`), so an
unrelated baseline never churns. It deletes baselines whose story or page no longer exists.
`visual:check` fails on such orphans.

## How a rendering is made deterministic

| Input | How it is fixed |
|---|---|
| Fonts | Every app and the workshop bundle Manrope (`@fontsource-variable/manrope`, imported by `console.css`), and the console bundles IBM Plex Mono, so they are served by the local static server. Only the storefront still loads Google Fonts; for it the harness intercepts `fonts.googleapis.com` and `fonts.gstatic.com`. It answers with CSS generated from the `@fontsource` packages pinned in the lockfile (one `@font-face` per requested weight and subset, the same shape as Google's). A family nobody has vendored fails the run instead of falling back to a system face (`lib/fonts.ts`). |
| Clock | `page.clock.setFixedTime("2026-09-15T10:30:00+05:30")`. `Date` is frozen and timers still run, so debounces and fetches behave normally. Fixture dates are written relative to this. |
| Locale, zone | `en-IN`, `Asia/Kolkata`. |
| Motion | `reducedMotion: "reduce"` (console.css turns off the page rise) and `animations: "disabled"` on every screenshot. Infinite animations such as the skeleton pulse are reset to their first frame. |
| Caret, scale | Caret hidden, `deviceScaleFactor: 1`, CSS-pixel screenshots. |
| Network | Only the local static server and the two font hosts are reachable. Any other request is refused, and the test fails on it. App API calls are answered from fixtures. An unmocked call gets a 501 and fails the test. |
| Readiness | Stories: Storybook's own render lifecycle reaches `finished`, which is after any `play` interaction. Pages: network idle, no skeleton or `aria-busy` left, and a named element visible, such as the page's `h1` or the table. Then `document.fonts.ready` and two frames. There are no sleeps. |
| Builds | Apps are photographed from their production `vite build`, not a dev server. |

**Tolerance.** `threshold: 0.05` (per-pixel colour distance) and `maxDiffPixels: 20`. Playwright's
default threshold of 0.2 would let a text colour move about 15% in luminance unnoticed, and that
is exactly the size of change a token re-value makes. Measurements on this machine:

- A bit-exact comparison (threshold 0, 0 px) of a second run showed 1–15 px of colour jitter on
  19 of 990 renderings, mostly dark-theme anti-aliasing.
- At 0.05 the same run had **zero** differing pixels on every rendering, twice.
- Re-valuing one token (`--color-faint`, `#9a9aa2 → #636b7a`) moved 118 px on a single text-field
  story, and the check failed as it should.

**Platforms.** Font rasterisation differs between macOS and Linux, so baselines are per OS
(`baselines/darwin/` today). A Linux run needs its own `baselines/linux/`, generated with
`pnpm visual:update` inside `mcr.microsoft.com/playwright:v1.63.0-noble`, with the same Playwright
version as the lockfile. This is why the harness runs in `make gate-full` (the local gate) and not
yet in the CI `frontend` job. GitHub Actions is unavailable, and there are no Linux baselines to
compare against.

## How a story is photographed

- By default the capture is the story's root element: the component and its padding, nothing else.
- A story tagged **`viewport`** is captured as exactly the viewport. Use this for overlays that
  position against it: dialogs, the mobile drawer, toasts, an open menu or combobox list.
- A story tagged **`fullpage`** is captured as the whole document. Use this for a page frame such
  as `AppShell`.
- Theme comes from the toolbar's Theme control (`&globals=theme:dark` in the iframe URL). The
  decorator in `.storybook/preview.tsx` drives the real `ThemeProvider` with it.
- A story that needs an interaction to reach its state (an open combobox, an open menu) uses a
  `play` function. The harness waits for it to finish. Stories never touch the network. An async
  prop such as `Combobox`'s `loadOptions` answers from a local list.

## Accessibility: the ratchet

For every rendering, axe runs the WCAG 2.0/2.1/2.2 A and AA rules (`wcag2a`, `wcag2aa`,
`wcag21a`, `wcag21aa`, `wcag22aa`; 2.2 AA includes `target-size`). App pages also get
`best-practice`: landmarks, one `h1`, heading order. Stories are fragments, so those rules would
flag every one of them.

`a11y-baseline.json` maps `surface|theme|width` to the number of offending nodes per rule. A run
**fails when any rule flags more nodes than recorded** on any rendering, including a rule that was
at zero. Fewer is reported as an improvement (the `a11y-improved` annotation in the report).
`visual:update` then lowers the number, so it cannot creep back. The file counts nodes rather
than CSS selectors on purpose. The redesign rewrites markup wholesale, and a selector-keyed list
would report every existing problem as new the first time a wrapper moved.

Baseline taken on 2026-09-25: 1,958 nodes on 560 of 990 renderings.

| Rule | Nodes | Renderings | Stories | Pages | Where |
|---|---|---|---|---|---|
| `color-contrast` | 1,864 | 546 | 100 | 6 | Mostly `--color-faint` text (2.79:1), the status tints, and white on the dark-theme accent `#8468ff` (primary buttons, active pills). The token re-value in audit §6.1 targets these. |
| `button-name` | 50 | 22 | 3 | 2 | `Button loading` hides its label with `visibility: hidden`, so the button has no name (Button, ConfirmDialog and FormDialog pending). The Deals Board/List switch has no name below `sm`. |
| `region` | 32 | 24 | 0 | 6 | Below `lg`, the compact top bar's product name (and the console's "Internal" badge) sits outside any landmark on every app page. |
| `empty-table-header` | 6 | 6 | 0 | 1 | Deals list: the actions column header is `""`. |
| `scrollable-region-focusable` | 6 | 6 | 1 | 0 | `Dialog` long content: the scrolling body is not keyboard-focusable. |

None of these were fixed in the slice that installed the harness. Its brief was to record them.

## API mocks for app pages

- `opsgrids-visual/fixtures/api/routes.ts`: which request gets which fixture. Each route names
  the OpenAPI operation it stands in for (`libs/contracts/openapi/<spec>.json`).
- `opsgrids-visual/fixtures/api/<app>/*.json`: the payloads. They describe one fictional
  organization ("Acme Industries") on the harness's frozen date. No app imports them, and no
  fixture data enters app code.
- `opsgrids-visual/fixtures/contracts.test.ts` validates every payload against its operation's
  response schema with ajv (JSON Schema 2020-12, which is what OpenAPI 3.1 uses). It also checks
  the route path against the operation's path template and the query keys against its declared
  parameters. If a service changes shape, `pnpm -r test` fails before a screenshot built on the
  old shape can pass.
- **Session.** `@opsgrids/client` mirrors the access token to `sessionStorage` so that a reload
  stays signed in. The console's own session module does the same. The harness uses that path. An
  init script puts an unsigned token (built from claims at run time, never committed) under the
  app's key before the app boots. `GET /auth/me` and `GET /members/me/grants` (or
  `/auth/operator/me`) are then ordinary mocked routes. This is the same seam the apps' unit tests
  stub (`authApi.me`), one layer further out. Nothing verifies the signature in a browser; the
  gateway does.

To add a page, add an entry to `lib/pages.ts` with a surface, app, path, a `ready` locator and an
optional `act` step. Then add routes and fixtures for every call it makes. The run lists any call
you missed. Then run `pnpm visual:update` and review.

## The layout sweep

Screenshots catch a change; they do not catch a layout that was wrong from the start. The sweep
(`opsgrids-visual/sweep/`) opens **every route of every app** in sample mode, at the phone widths
320, 360, 390 and 414 px (768, 1280 and 1600 are one `SWEEP_WIDTHS` away), and measures the page for the class of defect a person sees in a second: a button that
floats lower than the select beside it, a card something spills out of, a wrapped label that pushes
its input down, chart ticks that touch, a page with no room under its last line.

It is a developer's tool, **not part of `visual:check` or CI**: it starts a Vite dev server per app
(ports 5600-5699 only) and takes several minutes.

```bash
cd frontend
pnpm --filter @opsgrids/visual run sweep                                   # all 13 apps, the 4 phone widths
SWEEP_APPS=product-engage,product-finance pnpm --filter @opsgrids/visual run sweep
SWEEP_WIDTHS=768,1280 SWEEP_THEME=dark pnpm --filter @opsgrids/visual run sweep
SWEEP_ROUTE=/invoices SWEEP_APPS=product-finance pnpm --filter @opsgrids/visual run sweep
```

| Variable | Meaning | Default |
|---|---|---|
| `SWEEP_APPS` | comma list of package directories | all 13 |
| `SWEEP_WIDTHS` | viewport widths | `320,360,390,414` |
| `SWEEP_THEME` | `light` or `dark` | `light` |
| `SWEEP_ROUTE` | only routes whose path contains this | every route |
| `SWEEP_OUT` | where `report.json`, one JSON per app and `shots/` go | `opsgrids-visual/test-results/sweep` |
| `SWEEP_MAX_PAGES` | page cap per app | 140 |
| `SWEEP_WORKERS` | apps swept at once | 4 |

Routes come from each app's `main.tsx` (static routes), plus every in-app link the pages render
(that is how `/invoices/:id` gets a real id; two samples per parameterised route). A route that
redirects to sign-in or renders the not-found page is listed in the report as skipped, with the
reason. `opsgrids-dashboard` and `worker-app` have no sample mode, so their API is answered from
the harness's contract mocks and anything those do not cover answers as an empty list; their
populated states are therefore under-covered.

What it flags (the `cat` field of each hit):

| `cat` | Defect |
|---|---|
| `a` | The page scrolls sideways (`scrollWidth > clientWidth`); the widest in-flow element is named. |
| `b` | An element's box escapes its nearest bordered or shadowed ancestor. |
| `c` | Controls in one flex/grid row whose control boxes (not their wrappers) share no top, bottom or centre within 2 px. A caption under one control no longer hides this. |
| `d` | The same, for form fields: inputs in a row whose tops differ because a label wrapped or a helper sits under one. |
| `e` | Text clipped or wider than its box, text overlapping text, and chart axis labels closer than 8 px. |
| `f` | The last content is within 16 px of the bottom of the viewport once scrolled to the end. |
| `g` | An interactive target under 44x44 CSS px (button, link, tab, switch, checkbox with its label, text field under 44 tall). A `::before`/`::after` hit area counts (`hit-44`); an inline link in running prose does not. *Below 768 px only.* |
| `h` | A text field whose computed font-size is under 16 px: iOS Safari zooms the page on focus. |
| `j` | An open dialog, drawer or menu wider or taller than the viewport, or whose primary action is off-screen or clipped. Found by *probing*: the sweep clicks up to five openers per route (buttons that open a menu or sheet, "New ...", "Filters") and measures with the overlay open. |
| `k` | A table wider than the viewport with no scroll container of its own. |
| `l` | A fixed bar on a screen edge with no `env(safe-area-inset-*)`, a viewport meta without `viewport-fit=cover`, a sheet that ignores the insets, a bar covering the last content, a focused field hidden under a bar. |
| `m` | Text under 12 px (SVG text after its viewBox scale), body font under 14 px, table-cell text under 13 px. |
| `i` | Informational: text truncated with an ellipsis or line clamp and no `title` to read it in full. Counted, not a hit. |

**Who owns a hit.** Every hit names the component that *rendered* the offending element (`component`
in `report.json`; `components.json` groups by it). The name comes from React's dev-build creation stack
on the element's fiber: a file under `opsgrids-ui/src` is a shared component (`Button`, `DataTable`),
under `opsgrids-client/src` is `client/...`, anything else is `app-local`. `summary.txt` prints the
table, shared first. Fix shared hits once in `opsgrids-ui`; app-local ones in the app.

Every hit has a screenshot with the offending box in red (and the container or sibling in blue)
under `shots/<app>/`. `report.json` carries the selector signature, the measured numbers and the
width. The sweep is a detector, so it has false negatives: it cannot judge a populated state the
sample data does not produce, a modal, or anything behind an interaction.

Fix at the shared layer first (`opsgrids-ui`), then app-local; a hit is only acceptable when the
reason is written down. `opsgrids-visual/sweep/detector.mjs` is the in-page measurement; the notes
at the top of it are the definition of each category.

## How the later phases use this

- **Phase 2.1 token re-value.** Land it as one PR per concern: colour, then radius and shadow,
  then type. Each PR runs `visual:check`, reviews the (large, expected) diff in the report,
  updates, and commits the baselines with the code. That way each PR's image diff is about one
  thing. The contrast fixes should show up as `a11y-improved`. Updating locks them in.
- **Phase 2.x new and upgraded primitives** (StatusBadge, Tabs, Stepper, DataTable v2 and so on).
  Every new export needs a story before `pnpm -r test` passes, and every state gets a story.
  `visual:update` gives it baselines.
- **Phase 3 shell.** `AppShell` and `Sidebar` are already photographed at all three widths. The
  six app pages show the shell in place. Add the new shell's states (rail, bottom navigation, open
  sheets) as stories.
- **Phase 7 QA** (brief §22). Add 390, 430, 1024, 1440 and 1920 to `WIDTHS` in `lib/env.ts` when
  that phase starts. Every surface is then photographed at the full QA matrix.

## Things the baseline run surfaced (not fixed here)

- The operator console's `daysUntil` defaults "now" to a hard-coded `2026-08-24`
  (`opsgrids-internal/src/lib/format.ts`). Trial countdowns are therefore off by the number of
  days since then ("Ends in 26 days" for a trial that ends in 4), and the Overview's "trials about
  to lapse" list misses them.
- The console shows `trial_conversion_rate` and `churn_rate` with `formatPercent` without scaling
  them. The console service returns fractions (`round(x / y, 4)`), so the page shows 0.4% for 43%.
  The app's own fixtures use percentages, which hid this.
- `Dialog`'s heading takes a visible focus ring when it receives focus on open. The global
  `:focus-visible` rule in `console.css` is unlayered, so it beats Tailwind's layered
  `focus:outline-none`.

## Tool choices

| Tool | Why this and not the alternative |
|---|---|
| **Storybook 10.6** (`storybook`, `@storybook/react-vite`, no addons) | Measured against Ladle 5.1.1, the Vite-native alternative, in clean installs beside React 19 and Vite 7. Install weight is a wash: +90 MB for Storybook, +94 MB for Ladle. Ladle, though, pulls in 342 top-level packages against Storybook's 119, including **its own Vite 6, `@vitejs/plugin-react` 4 and a second esbuild**, beside the workspace's Vite 7. It has not been published since Nov 2025. Storybook 10.6 was released this month, takes Vite 7 as a peer, and builds the 159 stories in about 5 s. Its core already includes controls, toolbar globals, `play` interactions and an `index.json` the harness enumerates. No addon, no test-runner, no Chromatic (hosted, paid, and it needs the network). |
| **Playwright 1.63** (`@playwright/test`) | Screenshots with a per-pixel threshold, route interception for fonts and API, a fixed clock, per-OS snapshot paths and a pinned Chromium, all in one tool. |
| **@axe-core/playwright 4.13** | Runs axe in the same page, on the same rendering that was just photographed. There is no separate a11y pass to keep in sync. |
| **@fontsource-variable/manrope, @fontsource/ibm-plex-mono** | The apps' own fonts, pinned by the lockfile. The CDN is intercepted rather than replaced, so no app changes how it loads fonts. |
| **ajv 8** | Validates the mock payloads against the committed OpenAPI contracts. Without it, fixtures are hand-written guesses that drift. |
