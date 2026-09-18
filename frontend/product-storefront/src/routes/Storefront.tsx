import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { LayoutIcon } from "@opsgrids/ui";

import { getStore, listCategories, listProducts } from "@/api/client";
import type { Category, Product } from "@/api/types";
import { useElementWidth } from "@/lib/useElementWidth";
import type { StoreDoc } from "@/theme/schema";
import { STACK_WIDTH } from "@/theme/schema";
import { StoreRenderer } from "@/theme/render";
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

export interface StorefrontPageProps {
  /** The `PageDoc.route` this screen shows. */
  route: string;
  title: string;
  description: string;
}

/**
 * The buyer-facing store, framed by the merchant's console.
 *
 * Everything inside the frame is drawn by the renderer from the store document alone. The frame
 * itself — the heading, the width toggle, the states — is Console chrome and uses Console tokens;
 * nothing from one surface styles the other.
 */
export function StorefrontPage({ route, title, description }: StorefrontPageProps) {
  const load = useCallback(async (): Promise<StorefrontData> => {
    const [store, products, categories] = await Promise.all([
      getStore(),
      listProducts(),
      listCategories(),
    ]);
    return { store, products: products.items, categories };
  }, []);
  const state = useAsync(load, [route]);

  const [device, setDevice] = useState<DeviceKey>("desktop");
  const canvasRef = useRef<HTMLDivElement>(null);
  const measured = useElementWidth(canvasRef, 1200);
  const fixedWidth = DEVICE_WIDTHS[device];
  const width = fixedWidth ?? measured;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Preview"
        title={title}
        description={description}
        actions={
          <div className="flex items-center gap-2">
            <SampleChip title="Representative data — there is no storefront service yet, so the catalogue and the store document come from local fixtures" />
            <DeviceToggle value={device} onChange={setDevice} />
          </div>
        }
      />

      <p className="text-xs text-muted" aria-live="polite">
        {width < STACK_WIDTH
          ? `Canvas ${Math.round(width)}px — below ${STACK_WIDTH}px, so every section is full width and stacks in order.`
          : `Canvas ${Math.round(width)}px — sections sit on the 12-column grid.`}
      </p>

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="The store didn't load"
            message="Nothing was published or changed. Try again — if it keeps failing, the storefront service may be down."
            onRetry={() => window.location.reload()}
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
