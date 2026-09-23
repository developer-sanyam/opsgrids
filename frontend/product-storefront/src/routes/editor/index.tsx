import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { usePermissions, usePermissionStatus } from "@opsgrids/client";
import { Announcer, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "@opsgrids/ui";

import { getStore, getStoreBrand, listCategories, listProducts, saveStore } from "@/api/client";
import type { Category, Product } from "@/api/types";
import { SECTION_DEFS } from "@/theme/sections";
import type { Brand, StoreDoc } from "@/theme/schema";

import { EditorShell } from "./EditorShell";

/**
 * The editor screen: load the store, the catalogue it draws from, and hand them to the shell.
 *
 * Four states, all built: loading (with the three panes' space reserved so nothing jumps), error
 * with a retry, "no theme adopted yet" — which is where every merchant starts — and the editor
 * itself.
 */

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | {
      status: "ready";
      store: StoreDoc | null;
      brand: Brand | null;
      products: Product[];
      categories: Category[];
    };

/** The grant the storefront service requires to write a store document — reused from
 *  `storefront.store.settings.*` rather than a new code (ADR-0011 §3): anyone who can publish
 *  the store can edit its theme. */
const WRITE_PERMISSION = "storefront.store.settings.write";

function EditorScreen() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  // `unknown` is today's ordinary state — no browser-visible response states the caller's grants
  // yet — and it renders as editable rather than read-only: the server refuses the write either
  // way, and showing every merchant a read-only editor because the client cannot see a grant is
  // the worse of the two mistakes. A definite `denied` renders the read-only screen.
  const canEdit = usePermissionStatus(WRITE_PERMISSION, "ORG") !== "denied";
  const { entitlements } = usePermissions();
  const planGated = entitlements !== null && !entitlements.includes("storefront");

  const load = useCallback(() => {
    setState({ status: "loading" });
    Promise.all([getStore(), listProducts(), listCategories()])
      .then(async ([store, products, categories]) => {
        const brand = store ? await getStoreBrand(store.themeKey) : null;
        setState({ status: "ready", store, brand, products: products.items, categories });
      })
      .catch((error: unknown) =>
        setState({
          status: "error",
          message:
            error instanceof Error
              ? error.message
              : "The editor couldn't load your store. Try again.",
        }),
      );
  }, []);

  useEffect(load, [load]);

  if (planGated) {
    return (
      <Card className="p-6">
        <ErrorState
          title="Storefront isn't on your plan"
          tone="neutral"
          message="Your organization hasn't activated Storefront, so there's no store to design. An administrator can add it from billing."
        />
      </Card>
    );
  }

  return (
    <div className="rise flex flex-col gap-5">
      <PageHeader
        eyebrow="Storefront"
        title="Design your store"
        description="Drag sections onto the page, change every setting on them, and set the colours and type your store uses."
      />

      {state.status === "loading" ? <EditorSkeleton /> : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.message} onRetry={load} />
        </Card>
      ) : null}

      {state.status === "ready" && state.store === null ? (
        <Card className="p-6">
          <EmptyState
            title="You haven't adopted a theme yet"
            description="Pick one from the theme gallery. It's copied into your store, and every page, section and setting in it becomes yours to edit here."
            action={
              // A link, not a button in link's clothing: this navigates, and the design system's
              // Button renders a `<button>` with no `href` escape hatch.
              <Link
                to="/"
                className="text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
              >
                Browse themes
              </Link>
            }
          />
        </Card>
      ) : null}

      {state.status === "ready" && state.store !== null && state.brand !== null ? (
        <EditorShell
          store={state.store}
          brand={state.brand}
          library={SECTION_DEFS}
          products={state.products}
          categories={state.categories}
          canEdit={canEdit}
          onSave={saveStore}
        />
      ) : null}
    </div>
  );
}

/** The editor's own shape, reserved. A canvas that appears under a spinner shifts the whole page. */
function EditorSkeleton() {
  return (
    <Card className="flex flex-col overflow-hidden lg:h-[calc(100dvh-11rem)]">
      <div className="flex items-center justify-between gap-4 border-b border-line px-6 py-3">
        <Skeleton className="h-11 w-56" />
        <Skeleton className="h-9 w-64" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="flex shrink-0 flex-col gap-2 border-b border-line p-4 lg:w-72 lg:border-b-0 lg:border-r">
          <Skeleton className="h-6 w-28" />
          <Skeleton className="h-11 w-full" />
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-10 w-full rounded-tile" />
          ))}
        </div>
        <div className="min-h-96 flex-1 p-6">
          <Skeleton className="h-full w-full rounded-card" />
        </div>
        <div className="flex shrink-0 flex-col gap-3 border-t border-line p-4 lg:w-80 lg:border-l lg:border-t-0">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </Card>
  );
}

export function EditorPage() {
  return (
    <Announcer>
      <EditorScreen />
    </Announcer>
  );
}

export default EditorPage;
