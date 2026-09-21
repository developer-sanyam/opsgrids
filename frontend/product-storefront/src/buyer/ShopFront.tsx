/**
 * The shop, as a buyer meets it on the merchant's own address.
 *
 * ──────────────────────────────────────────────────────────────────────────────────────────
 * **What this draws, and from where.**
 *
 * `context.theme` — `tokens` and `pages` — arrives in the same `store-context` bootstrap this
 * component already had (ADR-0012 §11: the theme document extends that payload rather than
 * adding a second call on the first-paint path). The catalogue is a second, deliberate call —
 * `POST /storefront/public/catalogue` — made only once a theme exists to draw it with, so a
 * store with no theme never asks for products it has nowhere to put.
 *
 * A published store with **no theme adopted** is still a valid published store (ADR-0012 §11's
 * `theme: null` case) and gets its own honest empty state — a brand bar and a sentence — rather
 * than an error. A theme with **no products** is not this component's problem: every commerce
 * section already draws its own empty state from an empty `products` array, the same one a
 * merchant sees in the gallery's preview before they have written a catalogue.
 * ──────────────────────────────────────────────────────────────────────────────────────────
 *
 * **Escaping.** `name`, `tagline` and `logo_text` are merchant-authored and returned verbatim —
 * the service trims, bounds and strips control characters and nothing else. Every one of them is
 * rendered as a React text child, which escapes. There is no `dangerouslySetInnerHTML` on this
 * surface and none may be added.
 *
 * **This is pre-theme chrome, not a store page.** The brand bar below styles itself from the
 * app's own tokens, for the one state that has no theme to draw with yet. Once a theme exists,
 * `StoreRenderer` takes over entirely and draws with the *merchant's* tokens — this component
 * spends none of its own accent inside that tree, which is `theme/render/`'s isolation rule.
 */

import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import type { StoreContextOut } from "@/api/storeContext";
import { fetchPublicCatalogue } from "@/api/publicClient";
import type { CategoryPublic, ProductPublic } from "@/api/publicClient";
import { useElementWidth } from "@/lib/useElementWidth";
import type { PageDoc, StoreDoc, ThemeTokens } from "@/theme/schema";
import { StoreRenderer } from "@/theme/render";
import { NO_PAGES_BODY, NO_PAGES_TITLE, otherAddressNotice } from "./copy";

/**
 * The mark in the corner. **`logo_text` falls back to `name`** — both are nullable-by-contract in
 * the sense that `logo_text` may be `null`, and the server deliberately does not copy one into
 * the other, leaving the fallback to whatever renders the page. Three characters is what the
 * store chrome already uses for the same mark.
 */
function markFor(context: StoreContextOut): string {
  return (context.logo_text ?? context.name).trim().slice(0, 3).toUpperCase();
}

/**
 * The brand bar.
 *
 * Its geometry is shared with the loading skeleton — a 40px mark and two text lines inside the
 * same padding — so the switch from skeleton to shop moves nothing on the page.
 */
export function ShopHeader({ context }: { context: StoreContextOut }) {
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-4 py-4">
        <span
          aria-hidden
          className="grid size-10 shrink-0 place-items-center rounded-tile bg-ink text-sm font-extrabold text-surface"
        >
          {markFor(context)}
        </span>
        <div className="min-w-0">
          <h1 className="display truncate text-lg font-extrabold">{context.name}</h1>
          {context.tagline === null ? null : (
            <p className="truncate text-sm text-muted">{context.tagline}</p>
          )}
        </div>
      </div>
    </header>
  );
}

/** A published store with no theme adopted yet — a real, honest state, not an error. */
function NoTheme({ context }: { context: StoreContextOut }) {
  return (
    <div className="flex min-h-dvh flex-col bg-ground text-ink">
      <ShopHeader context={context} />
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-4 py-16">
        <div className="max-w-prose">
          <h2 className="display text-xl font-extrabold">{NO_PAGES_TITLE}</h2>
          <p className="mt-2 text-sm text-ink-soft">{NO_PAGES_BODY}</p>
        </div>
      </main>
      {context.is_canonical ? null : (
        <footer className="border-t border-line bg-surface">
          <p className="mx-auto w-full max-w-5xl px-4 py-3 text-xs text-muted">
            {otherAddressNotice(context.canonical_host)}
          </p>
        </footer>
      )}
    </div>
  );
}

interface CatalogueState {
  products: ProductPublic[];
  categories: CategoryPublic[];
}

const EMPTY_CATALOGUE: CatalogueState = { products: [], categories: [] };

/** The width to render at before the frame is measured — the viewport's own, so a phone never
 *  paints a desktop grid first. Mirrors `LiveStore.tsx`'s `initialWidth`. */
function initialWidth(): number {
  if (typeof document === "undefined") return 1200;
  return document.documentElement.clientWidth || window.innerWidth;
}

/** The theme's own tokens/pages, plus the brand this bundle already has — composed once per
 *  render rather than stored, since the buyer bundle holds no document state of its own. */
function composeStoreDoc(context: StoreContextOut): StoreDoc {
  const theme = context.theme;
  if (theme === null) throw new Error("composeStoreDoc called with no theme");
  return {
    themeKey: theme.theme_key,
    mode: theme.mode === "retail" ? "retail" : "trade",
    brand: {
      name: context.name,
      tagline: context.tagline ?? "",
      logoText: context.logo_text ?? "",
    },
    tokens: theme.tokens as unknown as ThemeTokens,
    pages: theme.pages as unknown as PageDoc[],
  };
}

/** The themed shop — fetches the catalogue once a theme exists, and renders the current path's
 *  page with it. A catalogue that fails to load degrades to an empty one rather than blanking
 *  the whole shop: the chrome, the brand and every other page still work. */
function ThemedShop({ context }: { context: StoreContextOut }) {
  const [catalogue, setCatalogue] = useState<CatalogueState>(EMPTY_CATALOGUE);
  const loadedFor = useRef<string | null>(null);

  useEffect(() => {
    if (loadedFor.current === context.store) return;
    loadedFor.current = context.store;
    let active = true;
    fetchPublicCatalogue({ store: context.store, size: 200 }).then(
      (result) => {
        if (active) setCatalogue({ products: result.products.items, categories: result.categories });
      },
      () => {
        /* Degrade to an empty catalogue. Every commerce section already has its own empty state,
           and a buyer who cannot see products is better served by a working page with none than
           by no page at all. */
      },
    );
    return () => {
      active = false;
    };
  }, [context.store]);

  const store = composeStoreDoc(context);
  const location = useLocation();
  const frameRef = useRef<HTMLDivElement>(null);
  const [fallbackWidth] = useState(initialWidth);
  const width = useElementWidth(frameRef, fallbackWidth);

  return (
    <div className="min-h-dvh" style={{ backgroundColor: store.tokens.colorBg }}>
      <div ref={frameRef} data-live-store className="grid min-h-dvh w-full">
        <StoreRenderer
          store={store}
          route={location.pathname === "" ? "/" : location.pathname}
          products={catalogue.products}
          categories={catalogue.categories}
          width={width}
          basePath=""
        />
      </div>
    </div>
  );
}

export function ShopFront({ context }: { context: StoreContextOut }) {
  if (context.theme === null) return <NoTheme context={context} />;
  return <ThemedShop context={context} />;
}
