import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { CheckIcon, LayoutIcon } from "@opsgrids/ui";

import { adoptTheme, listCategories, listProducts, listThemes, removeStore, getStore } from "@/api/client";
import type { Category, Product } from "@/api/types";
import type { StoreDoc, Theme } from "@/theme/schema";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  SampleChip,
  Skeleton,
  Tag,
  ThemePreview,
} from "@/ui";

interface GalleryData {
  themes: Theme[];
  products: Product[];
  categories: Category[];
  store: StoreDoc | null;
}

/** The two facts a card needs about the store, without re-fetching it per card. */
function adoptionState(store: StoreDoc | null, theme: Theme): "none" | "current" | "other" {
  if (!store) return "none";
  return store.themeKey === theme.key ? "current" : "other";
}

export function GalleryPage() {
  const load = useCallback(
    async (): Promise<GalleryData> => {
      const [themes, products, categories, store] = await Promise.all([
        listThemes(),
        listProducts(),
        listCategories(),
        getStore(),
      ]);
      return { themes, products: products.items, categories, store };
    },
    [],
  );
  const state = useAsync(load, []);

  // The adopted store is owned by the server; this holds only what this screen has changed since
  // it loaded, so nothing is duplicated into a store that would then drift.
  const [adopted, setAdopted] = useState<StoreDoc | null | undefined>(undefined);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [failure, setFailure] = useState<string | null>(null);

  const store = adopted === undefined ? (state.status === "success" ? state.data.store : null) : adopted;
  const currentThemeName =
    state.status === "success" && store
      ? (state.data.themes.find((t) => t.key === store.themeKey)?.name ?? store.themeKey)
      : null;

  async function runAdopt(theme: Theme) {
    if (busyKey) return;
    setBusyKey(theme.key);
    setFailure(null);
    setConfirmKey(null);
    try {
      const next = await adoptTheme(theme.key);
      setAdopted(next);
      setAnnouncement(`${theme.name} adopted. Your store now uses its pages.`);
    } catch (error) {
      setFailure(
        error instanceof Error ? error.message : "That theme couldn't be adopted. Try again.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function runRemove() {
    if (busyKey) return;
    setBusyKey("__remove__");
    setFailure(null);
    try {
      await removeStore();
      setAdopted(null);
      setAnnouncement("Store removed. Adopt a theme to start again.");
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "The store couldn't be removed.");
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Pick a theme"
        description="Each theme ships a full set of pages — home, catalogue and line detail — already composed. Adopting one copies its pages into your store, where you can edit them."
        actions={
          <div className="flex items-center gap-2">
            <SampleChip title="Representative data — there is no storefront service yet, so the catalogue and the store document come from local fixtures" />
            {store ? (
              <Button
                variant="outline"
                onClick={() => void runRemove()}
                disabled={busyKey !== null}
              >
                {busyKey === "__remove__" ? "Removing…" : "Remove this store"}
              </Button>
            ) : null}
            {/* Navigation is an `<a>`, never a button that navigates — the house pattern for a
                link wearing the primary action's skin (see product-hrms `ComingSoon`). */}
            <Link
              to="/store"
              className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
            >
              View the store
            </Link>
          </div>
        }
      />

      {/* Announced without stealing focus: adopting replaces the page set, which is a change a
          keyboard or screen-reader user must hear even though nothing moved on screen. */}
      <p aria-live="polite" role="status" className="sr-only">
        {announcement}
      </p>

      {failure ? (
        <InlineAlert tone="danger" title="That didn't work">
          {failure}
        </InlineAlert>
      ) : null}

      {currentThemeName ? (
        <InlineAlert
          tone="info"
          live="off"
          title={`Your store is running the ${currentThemeName} theme`}
          action={
            <Link
              to="/store"
              className="inline-flex h-9 items-center rounded-full border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-ground"
            >
              Open it
            </Link>
          }
        >
          Adopting a different theme replaces every page with that theme's pages. Your brand details
          are kept; edits to the current pages are not.
        </InlineAlert>
      ) : null}

      {state.status === "loading" ? (
        // Reserves the card grid's height so nothing jumps when the themes arrive.
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Card key={i} className="p-4">
              <Skeleton className="h-58" />
              <Skeleton className="mt-4 h-5 w-1/2" />
              <Skeleton className="mt-2 h-4 w-3/4" />
              <Skeleton className="mt-4 h-9 w-40" />
            </Card>
          ))}
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="The theme library didn't load"
            message="Nothing was changed. Try again, and if it keeps failing the storefront service may be down."
            onRetry={() => window.location.reload()}
          />
        </Card>
      ) : null}

      {state.status === "success" && state.data.themes.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<LayoutIcon className="size-6" />}
            title="No themes are available"
            description="This build ships no themes. That is a packaging problem rather than something you can fix here — tell support."
          />
        </Card>
      ) : null}

      {state.status === "success" && state.data.themes.length > 0 ? (
        <ul className="grid list-none gap-4 p-0 md:grid-cols-2 xl:grid-cols-3">
          {state.data.themes.map((theme) => {
            const adoption = adoptionState(store, theme);
            const busy = busyKey === theme.key;
            return (
              <li key={theme.key}>
                <Card className="flex h-full flex-col gap-4 p-4">
                  <ThemePreview
                    theme={theme}
                    products={state.data.products}
                    categories={state.data.categories}
                  />
                  <div className="flex flex-col gap-1">
                    <div className="flex items-start justify-between gap-2">
                      <h2 className="text-lg font-bold tracking-tight">{theme.name}</h2>
                      {adoption === "current" ? (
                        <Tag tone="positive">
                          <CheckIcon className="mr-1 size-3" />
                          In use
                        </Tag>
                      ) : null}
                    </div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted">
                      {theme.industry}
                    </p>
                    <p className="text-sm text-muted">{theme.description}</p>
                    <p className="text-xs text-faint">
                      {theme.pages.length} pages ·{" "}
                      {theme.pages.reduce((total, page) => total + page.sections.length, 0)} sections
                    </p>
                  </div>

                  <div className="mt-auto flex flex-wrap items-center gap-2">
                    {adoption === "current" ? (
                      <Link
                        to="/store"
                        className="inline-flex h-11 items-center rounded-full border border-line bg-surface px-5 font-semibold text-ink transition-colors hover:bg-ground"
                      >
                        View the store
                      </Link>
                    ) : confirmKey === theme.key ? (
                      <>
                        <Button
                          variant="danger"
                          onClick={() => void runAdopt(theme)}
                          disabled={busy}
                        >
                          {busy ? "Replacing…" : "Replace my pages"}
                        </Button>
                        <Button variant="ghost" onClick={() => setConfirmKey(null)} disabled={busy}>
                          Keep what I have
                        </Button>
                      </>
                    ) : (
                      <Button
                        onClick={() =>
                          adoption === "other" ? setConfirmKey(theme.key) : void runAdopt(theme)
                        }
                        disabled={busyKey !== null}
                      >
                        {busy ? "Adopting…" : "Adopt this theme"}
                      </Button>
                    )}
                  </div>
                  {confirmKey === theme.key ? (
                    <p className="text-xs text-muted">
                      This discards the pages in your current store. Your brand details are kept.
                    </p>
                  ) : null}
                </Card>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
