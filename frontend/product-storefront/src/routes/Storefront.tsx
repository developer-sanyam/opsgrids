import { useCallback, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { LayoutIcon, Select } from "@opsgrids/ui";

import { getStore, listCategories, listProducts } from "@/api/client";
import type { Category, Product } from "@/api/types";
import { nameFromRoute } from "@/editor/pageRoutes";
import { useElementWidth } from "@/lib/useElementWidth";
import { CONSOLE_STORE_BASE } from "@/routes/live/liveRoutes";
import type { PageDoc, StoreDoc } from "@/theme/schema";
import { STACK_WIDTH } from "@/theme/schema";
import { StoreRenderer, storeHref } from "@/theme/render";
import { pageForRoute } from "@/theme/store";
import {
  Card,
  DEVICE_WIDTHS,
  DeviceToggle,
  EmptyState,
  ErrorState,
  PageHeader,
  SampleChip,
  Skeleton,
} from "@/ui";
import type { DeviceKey } from "@/ui";

interface StorefrontData {
  store: StoreDoc | null;
  products: Product[];
  categories: Category[];
}

/** The height the canvas reserves before the store arrives, so the page does not jump. */
const RESERVED_HEIGHT = 720;

/**
 * The console's own words for the three pages every theme ships.
 *
 * These are the only routes this screen can describe without reading the document — they are the
 * ones the section library and the renderer name themselves, so what a merchant is looking at is
 * known before the store loads. Every other address is a page the merchant wrote, and the document
 * is the only thing that can name it.
 */
const THEME_PAGES: Record<string, { title: string; description: string }> = {
  "/": {
    title: "Your storefront",
    description:
      "The home page a dealer lands on, drawn from your store document. Switch the preview width to see how the 12-column grid stacks.",
  },
  "/catalogue": {
    title: "Catalogue page",
    description: "Every line you carry, composed from the same section library as the home page.",
  },
  "/product": {
    title: "Line detail page",
    description:
      "One catalogue line, with its specification, related prices and the enquiry form.",
  },
};

/** What the frame is showing, in the merchant's words. */
function heading(route: string, page: PageDoc | null, loaded: boolean) {
  const themePage = THEME_PAGES[route];
  if (themePage) return themePage;
  if (page) {
    return {
      title: page.name,
      description:
        "A page you added to your store. Everything inside the frame is drawn from the sections you placed on it.",
    };
  }
  return {
    // Named from its address until the document arrives with the name the merchant gave it, so
    // the heading is never a slug on screen and never empty while the store loads.
    title: nameFromRoute(route),
    description: loaded
      ? "Your store has no page at this address. It may have been deleted, or the address may be mistyped."
      : "Loading this page from your store document.",
  };
}

/**
 * The `PageDoc.route` this URL is asking for.
 *
 * The console's preview is a splat, exactly as `/live/*` is, so the segment after `/store` is the
 * store's own route and nothing here holds a list of pages. `liveRoutes.storeRouteForPath` answers
 * the same question for `/live` by parsing the pathname; it is bound to that base and is another
 * unit's file this cycle, so rather than copy its parser the router's own splat parameter is read —
 * same shape, same pass-through of unknown routes, one implementation each and neither guessing.
 */
function routeFromSplat(splat: string): string {
  const trimmed = splat.replace(/\/+$/, "");
  return trimmed === "" ? "/" : `/${trimmed}`;
}

/**
 * The buyer-facing store, framed by the merchant's console.
 *
 * Everything inside the frame is drawn by the renderer from the store document alone. The frame
 * itself — the heading, the page switcher, the width toggle, the states — is Console chrome and
 * uses Console tokens; nothing from one surface styles the other.
 */
export function StorefrontPage() {
  const params = useParams();
  const route = routeFromSplat(params["*"] ?? "");
  const navigate = useNavigate();

  const [attempt, setAttempt] = useState(0);
  const load = useCallback(async (): Promise<StorefrontData> => {
    const [store, products, categories] = await Promise.all([
      getStore(),
      listProducts(),
      listCategories(),
    ]);
    return { store, products: products.items, categories };
  }, []);
  // Loaded once for the whole preview rather than once per page: one component now stays mounted
  // across every store address, so `attempt` is the retry and the address is not a reason to
  // re-fetch a document that has not changed.
  const state = useAsync(load, [attempt]);

  const [device, setDevice] = useState<DeviceKey>("desktop");
  const canvasRef = useRef<HTMLDivElement>(null);
  const measured = useElementWidth(canvasRef, 1200);
  const fixedWidth = DEVICE_WIDTHS[device];
  const width = fixedWidth ?? measured;

  const store = state.status === "success" ? state.data.store : null;
  const page = store ? pageForRoute(store, route) : null;
  const { title, description } = heading(route, page, state.status === "success");

  /*
   * The page switcher, built from the document every time it is drawn.
   *
   * This is the list the sidebar used to hold as three fixed links, and holding it there was the
   * bug: a merchant's thirty-sixth page cannot appear in a constant. Here it cannot drift — the
   * options *are* the store's pages — and it reaches the pages the store's own navigation
   * deliberately does not, which is the one thing the framed store below cannot do for itself.
   *
   * Before the document arrives it holds the address being previewed and is disabled, so the
   * control is the same size on every state and nothing moves when the store lands.
   */
  const pages = store?.pages ?? [];
  const known = pages.some((candidate) => candidate.route === route);
  const options = [
    ...(known ? [] : [{ value: route, label: title, disabled: true }]),
    ...pages.map((candidate) => ({ value: candidate.route, label: candidate.name })),
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Preview"
        title={title}
        description={description}
        actions={
          // Wraps: the three width pills and the sample chip are 395px of controls, which pushed a
          // horizontal scrollbar onto the whole console at a 390px viewport. Measured in Chrome at
          // 390 and 360 — the pills fit on a line of their own at both.
          <div className="flex flex-wrap items-center justify-end gap-2">
            <SampleChip title="Representative data — there is no storefront service yet, so the catalogue and the store document come from local fixtures" />
            <DeviceToggle value={device} onChange={setDevice} />
          </div>
        }
      />

      <div className="flex flex-wrap items-end justify-between gap-4">
        <Select
          label="Page"
          value={route}
          options={options}
          disabled={pages.length === 0}
          onChange={(event) => void navigate(storeHref(event.target.value, CONSOLE_STORE_BASE))}
          containerClassName="w-full max-w-xs"
        />
        <p className="text-xs text-muted" aria-live="polite">
          {width < STACK_WIDTH
            ? `Canvas ${Math.round(width)}px — below ${STACK_WIDTH}px, so every section is full width and stacks in order.`
            : `Canvas ${Math.round(width)}px — sections sit on the 12-column grid.`}
        </p>
      </div>

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="The store didn't load"
            message="Nothing was published or changed. Try again — if it keeps failing, the storefront service may be down."
            onRetry={() => setAttempt((n) => n + 1)}
          />
        </Card>
      ) : null}

      {state.status === "success" && !state.data.store ? (
        <Card className="p-6">
          <EmptyState
            icon={<LayoutIcon className="size-6" />}
            title="No theme adopted yet"
            description="Your store has no pages because no theme has been adopted. Pick one from the gallery and its pages are copied into your store."
            action={
              <Link
                to="/"
                className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
              >
                Pick a theme
              </Link>
            }
          />
        </Card>
      ) : null}

      {/* The canvas is always in the tree so its width is measured on the first paint and the
          reserved height matches the rendered height. */}
      <div className="flex justify-center">
        <div
          ref={canvasRef}
          className="w-full overflow-hidden rounded-card shadow-card transition-[max-width] duration-200"
          style={{ maxWidth: fixedWidth ?? "100%" }}
        >
          {state.status === "loading" ? (
            // The skeleton occupies the canvas itself, at the height the store will take, so the
            // page does not shift when it resolves.
            <div
              className="flex flex-col gap-4 bg-surface p-6"
              style={{ height: RESERVED_HEIGHT }}
              aria-hidden
            >
              <Skeleton className="h-12 w-1/3 rounded-tile" />
              <Skeleton className="h-56 rounded-tile" />
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Skeleton className="h-40 rounded-tile" />
                <Skeleton className="h-40 rounded-tile" />
                <Skeleton className="h-40 rounded-tile" />
                <Skeleton className="h-40 rounded-tile" />
              </div>
            </div>
          ) : null}
          {state.status === "success" && state.data.store ? (
            <StoreRenderer
              store={state.data.store}
              route={route}
              products={state.data.products}
              categories={state.data.categories}
              width={width}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
