import { useCallback, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { usePermissions, useAsync } from "@opsgrids/client";

import { getStore, listCategories, listProducts } from "@/api/client";
import type { Category, Product } from "@/api/types";
import { useElementWidth } from "@/lib/useElementWidth";
import type { StoreDoc } from "@/theme/schema";
import { StoreRenderer } from "@/theme/render";
import { pageForRoute } from "@/theme/store";
import { Card, ErrorState, Skeleton } from "@/ui";

import { PreviewBar } from "./live/PreviewBar";
import {
  documentTitle,
  faviconDataUri,
  LIVE_BASE,
  liveTargetForConsolePath,
  storeRouteForPath,
} from "./live/liveRoutes";
import { useLiveDocument } from "./live/useLiveDocument";
import { useLiveScroll } from "./live/useLiveScroll";

/**
 * The store as a buyer meets it.
 *
 * `/store` is the same document inside the merchant's console — page header, device toggle, sample
 * chip, sidebar. This route is the other half of that pair: one component for every store page,
 * the whole viewport, and no console furniture at any width. The store's own header and footer are
 * the only chrome on the screen, and the store's `maxWidth` token — not the console's shell — is
 * what decides how wide the content column runs.
 *
 * Its address space is a splat: `/live` is the home page, `/live/catalogue` and `/live/product` are
 * the two others every theme ships, and a theme that later adds `/about` gets a working address
 * with no change here. One component stays mounted across all of them, so moving between store
 * pages re-renders rather than re-fetches — a site, not three screens that each boot.
 */

interface LiveStoreData {
  store: StoreDoc | null;
  products: Product[];
  categories: Category[];
}

/** The grant a merchant's organization needs for Storefront to be theirs at all. */
const STOREFRONT_ENTITLEMENT = "storefront";

/**
 * The reason a visitor is bounced to the gallery, carried on the navigation.
 *
 * The gallery does not render it yet — it is owned elsewhere this cycle — so this is a seam, not a
 * finished sentence on screen. `?from=live` is in the URL for the same reason: the destination can
 * pick either up without this route changing again.
 */
const NO_STORE_NOTICE =
  "There is no live store yet because no theme has been adopted. Pick one and its pages become your store.";

/**
 * The width to render at before the first measurement — the viewport's, so 390px never paints a
 * desktop grid first.
 *
 * `documentElement.clientWidth` is the *layout* viewport and `innerWidth` is the visual one; on a
 * phone that has shrunk a page to fit they disagree (445 against 390 on the case this was found
 * on). It is only the pre-measurement fallback either way — the frame's own measured width
 * replaces it as soon as the frame exists, which is the whole point of measuring.
 */
function initialWidth(): number {
  if (typeof document === "undefined") return 1200;
  return document.documentElement.clientWidth || window.innerWidth;
}

/** A full-viewport surface for the states that have no store to draw with yet. */
function LiveInterlude({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-dvh place-items-center bg-ground p-4">
      <Card className="w-full max-w-md p-6">{children}</Card>
    </div>
  );
}

/** The way out of a live page that has no store on it. */
function BackToConsole() {
  return (
    <Link to="/" className="text-sm font-semibold text-accent hover:underline">
      Back to OpsGrids Storefront
    </Link>
  );
}

export function LiveStorePage() {
  const [attempt, setAttempt] = useState(0);
  const load = useCallback(async (): Promise<LiveStoreData> => {
    const [store, products, categories] = await Promise.all([
      getStore(),
      listProducts(),
      listCategories(),
    ]);
    return { store, products: products.items, categories };
  }, []);
  // The same three calls the console's preview makes, loaded once for the whole site rather than
  // once per page: `attempt` is the retry, not the address.
  const state = useAsync(load, [attempt]);

  const location = useLocation();
  const navigate = useNavigate();
  const storeRoute = storeRouteForPath(location.pathname);

  const { entitlements } = usePermissions();
  const planGated = entitlements !== null && !entitlements.includes(STOREFRONT_ENTITLEMENT);

  const frameRef = useRef<HTMLDivElement>(null);
  const [fallbackWidth] = useState(initialWidth);
  const width = useElementWidth(frameRef, fallbackWidth);

  useLiveScroll();

  const store = state.status === "success" ? state.data.store : null;
  const page = store ? pageForRoute(store, storeRoute) : null;
  useLiveDocument(
    store
      ? {
          title: documentTitle(store.brand.name, storeRoute, page?.name ?? null),
          favicon: faviconDataUri(
            store.brand.logoText,
            store.tokens.colorAccent,
            store.tokens.colorAccentText,
          ),
          background: store.tokens.colorBg,
        }
      : null,
  );

  /**
   * Keep in-store navigation inside `/live`.
   *
   * The store's own chrome no longer needs this: `StoreRenderer` takes a `basePath` below, so the
   * navigation is built against `/live` and — the part that was actually broken — `NavLink` can see
   * which page is current here.
   *
   * What still needs catching is a **section's** call to action. Those hrefs are merchant data:
   * `ctaHref: "/store/catalogue"` is stored in the theme's settings, so no prop on the renderer can
   * reach them. Catching the click in the capture phase, where the router's own `Link` sees
   * `defaultPrevented` and stands down, keeps a buyer on the buyer's site. Modified clicks, new-tab
   * targets and every address that is not a store page are left alone.
   */
  const keepNavigationLive = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!(event.target instanceof Element)) return;
      const anchor = event.target.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (anchor.target !== "" && anchor.target !== "_self") return;
      const url = new URL(anchor.href, window.location.origin);
      if (url.origin !== window.location.origin) return;
      const target = liveTargetForConsolePath(url.pathname);
      if (target === null) return;
      event.preventDefault();
      void navigate(`${target}${url.search}${url.hash}`);
    },
    [navigate],
  );

  if (planGated) {
    return (
      <LiveInterlude>
        <ErrorState
          tone="neutral"
          title="This store isn't available"
          message="Storefront isn't active for this organization, so there is no store to show. An administrator can add it from billing."
          action={<BackToConsole />}
        />
      </LiveInterlude>
    );
  }

  if (state.status === "error") {
    // A 401 or 403 from the gateway lands here too: the server decides who may read this store,
    // and this route only reports what it was told. Nothing is hidden client-side instead.
    return (
      <LiveInterlude>
        <ErrorState
          title="This store didn't load"
          message={
            typeof navigator !== "undefined" && navigator.onLine === false
              ? "You appear to be offline. Reconnect and try again — nothing was lost."
              : "Nothing was changed. Try again — if it keeps failing, the storefront service may be down."
          }
          onRetry={() => setAttempt((n) => n + 1)}
          action={<BackToConsole />}
        />
      </LiveInterlude>
    );
  }

  // No theme adopted: there is no store to be a visitor of. An empty shop front with working
  // navigation would be a lie; the gallery is where this ends.
  if (state.status === "success" && state.data.store === null) {
    return <Navigate to="/?from=live" replace state={{ notice: NO_STORE_NOTICE }} />;
  }

  if (state.status === "loading" || store === null) {
    // The same shape the store will take, at the same full-bleed width, so the switch to the real
    // thing moves nothing. Neutral rather than themed: the tokens arrive with the store.
    return (
      <div className="min-h-dvh bg-ground">
        <p className="sr-only" role="status" aria-live="polite">
          Loading the store
        </p>
        <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col gap-6 p-4" aria-hidden>
          <Skeleton className="h-12 rounded-tile" />
          <Skeleton className="h-72 rounded-tile" />
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Skeleton className="h-44 rounded-tile" />
            <Skeleton className="h-44 rounded-tile" />
            <Skeleton className="h-44 rounded-tile" />
            <Skeleton className="h-44 rounded-tile" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-dvh" style={{ backgroundColor: store.tokens.colorBg }}>
      <PreviewBar />
      {/* `grid` with one child stretches the store to the full viewport height, so a short page
          ends in the store's own background rather than the console's grey. The bottom padding is
          the preview control's clearance — it is inside the store's colour, so it reads as the end
          of the page and not as a band. */}
      <div
        ref={frameRef}
        data-live-store
        onClickCapture={keepNavigationLive}
        className="grid min-h-dvh w-full pb-16"
      >
        <StoreRenderer
          store={store}
          route={storeRoute}
          products={state.data.products}
          categories={state.data.categories}
          width={width}
          basePath={LIVE_BASE}
        />
      </div>
    </div>
  );
}
