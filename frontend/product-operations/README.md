# product-operations

The standalone **Operations** product app (ADR-0001) — procurement, inventory and the paperwork
between them. Vite + React + TypeScript + Tailwind CSS v4. Visual language is the shared "Console"
system: Manrope, a single violet accent (`#6C4CF1`) spent sparingly, big-radius cards on a
light-grey ground, a grouped left sidebar with an account switcher and ⌘K search, and a full
light/dark theme (toggle in the sidebar).

## Run it

```bash
pnpm --filter @opsgrids/product-operations dev        # http://localhost:4380
pnpm --filter @opsgrids/product-operations build
pnpm --filter @opsgrids/product-operations typecheck
pnpm --filter @opsgrids/product-operations lint
```

Dev serves on **:4380** (`--strictPort`), clear of the marketing site (4300) and product-hrms
(4310). In dev, Vite proxies `/api/*` to the gateway on **:8100** (`server.proxy` in
`vite.config.ts`), so the browser makes only same-origin calls and CORS never enters the picture.

## Fixtures-first

The Operations backend is still a skeleton, so the app is **fixtures-first**: `USE_FIXTURES`
defaults **true** and the whole UI renders from `src/api/fixtures.ts` with a stand-in identity and
no login. Flip it once the service ships:

```ts
// src/api/client.ts
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";
```

Set `VITE_USE_FIXTURES=false` and every client function falls through to the gateway at
`/api/v1/{suppliers,purchase-orders,inventory}` with the bearer token from the session — wiring the
live backend is a flag flip, not a rewrite. All money is carried as **integer minor units** (paise)
with an ISO currency and only becomes a display string in `src/lib/format.ts`.

## Screens

- **Overview** — a breadcrumb + title bar with search / period / New PO, a row of four KPI cards
  (Open POs, Inventory value, Low-stock items, On-time delivery %), then Spend by Category (live
  bars), a Spend Over Time area chart, and a PO Status donut, and finally recent purchase orders
  beside a **Needs Attention** queue (low stock + late deliveries) and a Top Suppliers list. Every
  figure derives from the purchase-order / inventory / supplier payloads.
- **Purchase Orders** — a sortable, searchable table (PO number, supplier, items, total, status,
  expected date) with status filter chips and row drill-down.
- **PO detail** — line items footing to a total, a delivery-status banner (on-time / overdue /
  received), supplier and warehouse meta, notes, and a **Mark as received** affordance.
- **Inventory** — a stock table (SKU, item, warehouse, on-hand, reorder point, value) with
  labelled low-stock / out-of-stock flags and a low-stock quick filter.
- **Suppliers** — a sortable table (name, category, lead time, on-time %, spend, status) with
  drill-down to a supplier detail page (stat tiles, contact card, and that supplier's POs).
- **Operations / Workspace** — Receiving, Warehouses, Reports, Integrations, Team Members, Settings
  and Help are routed "coming soon" placeholders reachable from the sidebar.

### Warehouse map, floor view and Find stock

Live-only screens (no fixture twin), all built so that no failure in them can break another screen.

- **Warehouse map** (`/warehouses/map`) — every warehouse on a Google map, and always as a list
  beside it; warehouses with no position are listed apart with "Set location". The warehouse dialog
  takes the site position as degrees and sends integer micro-degrees.
- **Floor view** (`/warehouses/:id/floors/:floorId/view`) — a floor's *published* layout as a 2-D plan
  (SVG, no WebGL) and a 3-D scene. `?highlight=<location ids>` marks what a search found; `?view=3d`
  picks the tab. The 3-D page (`/warehouses/:id/3d`) has a floor selector (`?floor=`): a floor with a
  published layout is drawn from its real placements, anything else keeps the auto-arranged layout
  and its banner.
- **Floor layout editor** (`/warehouses/:id/floors/:floorId`) — places a floor's locations, draws the
  walk graph, validates and publishes a draft. An empty canvas says so, with the count of unplaced
  locations and two ways forward: **Auto-arrange** puts every unplaced location onto the canvas in
  one step (grouped by parent — a band per zone, then a row per depth, siblings together, an aisle
  between parents — sized by kind and ordered by pick sequence then code, beneath anything already
  placed), and **Place** adds one into the first free spot and selects it, bringing it into view and
  leaving keyboard focus on the next Place button. Both leave the draft unsaved; *Undo* is offered
  until the next edit.
  **Which locations are "unplaced".** A location records its floor only when a layout is published
  (`Location.floor_id`), publishing a floor writes that floor onto every location it places, and
  Find stock and put-away guidance read a location on two published floors as being on whichever was
  published last. So "unplaced" means on no floor and not on this draft: the editor reads the
  published layout of each *other* floor of the warehouse and sets the locations they hold apart.
  Auto-arrange and the Unplaced list never include them; they are listed beneath, under their floor,
  and each is placed only by "Place here too" after a confirmation. Until those reads have landed
  (or if one fails) nothing is offered and Auto-arrange is off, with a Try again — it will not guess.
  A location only in another floor's unpublished draft is not counted: it is not on that floor yet.
  The warehouse's locations are read in full, every page (`listLocations`), so "all N" and "every
  location is on the floor" are about the whole tree.
  Limits worth knowing: footprints are schematic defaults by kind (edit them in the selection panel),
  and never nest, because the server rejects overlapping footprints — a rack is not drawn around
  its bins. Auto-arrange adds no walk nodes (one per location with no edges between them would fail
  the connectivity check on every one); *Place* still adds its location's node. The editor says how
  many placed locations have no walk node, and *Try a route* lists only those that have one. *Check
  for problems* reads the saved draft, not unsaved edits. Past 1,000 m (the server's coordinate
  bound) what does not fit stays unplaced, and the note says how many. Nothing on the server stops a
  location being placed on two floors' drafts; the editor's separation is the only guard.
  **Touch.** Zoom is the canvas's width. A tap selects, adds a walk node or connects one, and a
  finger that moves is never a tap. With **Edit by touch** off (the default) the browser pans the
  plan and the page as it does anywhere. On, the canvas holds the touch so a drag on the *selected*
  shape moves it (or resizes it, from its handle) and every other drag pans the plan — by script,
  not by the browser: Chrome ignores `touch-action` on shapes inside an SVG, so it cannot be given
  to the selected shape alone. A pen is treated as a finger. Resize handles and walk nodes are drawn
  a few screen pixels across wherever the plan is scaled down to fit, and on a device with a coarse
  pointer the selected shape's handle (and every node, while connecting) has a 44px target. The zoomed
  canvas is a tab stop, so the arrow keys pan it. There is no pinch gesture of its own.
- **Find stock** — a card on the Stock page and on item, lot and roll detail. It asks
  `GET /inventory/locate` only when clicked and groups the bins by warehouse and floor.
- **Put-away details** — on a storage put-away's ledger row. The worker's GPS fix is behind "Show
  worker location", offered only with `operations.inventory.putaway.location_read`; it is a POST, is
  audited on the server, and the result lives in that drawer's state only.

**The Google Maps key** is `VITE_GOOGLE_MAPS_API_KEY`, read at build time and baked into the bundle
(`infrastructure/edge/publish-web.sh` sets it for this app only), so it must be referrer- and
API-restricted in the Google console. **A build without it is a complete build**: the map page is the
list, the dialog has only the manual latitude and longitude fields, the reveal shows coordinates as
text, and the Maps loader (its own lazy chunk) is never requested. The same fallbacks apply when the
script fails to load or Google refuses the key (`window.gm_authFailure`). Pins use the classic
`Marker`, which needs no Cloud Map ID; Google logs a deprecation notice for it, and switching to
`AdvancedMarker` needs a Map ID.

## Auth

Sign-in lives in [`src/auth/`](src/auth). `AuthProvider` owns the session; `useAuth` reads it;
`RequireAuth` gates the app and bounces to `/login` when there is no token. The access token is
held **in memory** and mirrored to `sessionStorage` (never `localStorage`); the refresh token is an
httpOnly cookie. In fixtures mode the guard is bypassed with a stand-in identity.

## Components

A local set under [`src/ui/`](src/ui): `Button`, `Pill`, `Card`, `KpiCard`, `Sparkline`,
`DeltaPill`, `PageHeader`, `DataTable` (sortable + drill-down), `PoStatusPill` / `SupplierStatusPill`
/ `StockPill`, `SpendTrend`, `CategoryBreakdown`, `StatusMix`, `NeedsAttention`, `TopSuppliers`,
`Avatar`, `Tag`, `SearchInput`, `Sidebar`, `ThemeToggle`, and shared `Skeleton` / `EmptyState` /
`ErrorState` / `SectionHeader`. Charts are hand-rolled inline SVG — no charting dependency.

> **Follow-up:** these should **graduate to the shared `opsgrids-ui` package** — ADR-0001 forbids an
> app forking shared components long-term. They live here only for this first product slice.

## Deferred (next slices)

- **Operations service** — wire live suppliers, purchase orders and inventory (flip `USE_FIXTURES`).
- **Receiving** — post real goods-receipt notes that decrement stock.
- **`opsgrids-ui` graduation** — move `src/ui/*` into the shared design-system package.
