import { useMemo, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { EmptyState, PlusIcon, TagIcon, useToast } from "@opsgrids/ui";

import { getCatalog, listOrganizations } from "@/api/client";
import type { OverrideResult, PlanVersion, Price, Product } from "@/api/types";
import { useOperatorAuth } from "@/auth/context";
import { formatDate, formatInt } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import {
  Button,
  Card,
  DataTable,
  EnvBadge,
  LoadFailure,
  PageHeader,
  Pill,
  PlanVersionPill,
  SectionHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { BespokePriceDialog, acceptedDescription } from "./overrides";

function priceLabel(price: Price): string {
  if (price.kind === "flat") return `${formatMoney(price.amount_minor, price.currency)} / mo`;
  if (price.kind === "per_seat") {
    return `${formatMoney(price.amount_minor, price.currency)} / ${price.unit ?? "seat"}`;
  }
  const rate = price.unit_price_micros ?? 0;
  return `${formatMoney(Math.round(rate / 10_000), price.currency, { precise: true })} / ${price.unit ?? "unit"}`;
}

function ProductSection({ product, versions }: { product: Product; versions: PlanVersion[] }) {
  const columns: Column<PlanVersion>[] = [
    {
      key: "plan",
      header: "Plan · version",
      render: (v) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{v.plan_name}</span>
          <span className="money text-xs text-faint">v{v.version_no}</span>
          {v.bundled_products && v.bundled_products.length > 0 ? (
            <span className="text-xs text-muted">
              Bundles <span className="money">{v.bundled_products.join(", ")}</span>
            </span>
          ) : null}
        </div>
      ),
    },
    { key: "state", header: "State", render: (v) => <PlanVersionPill state={v.state} /> },
    {
      key: "audience",
      header: "Audience",
      render: (v) =>
        v.restricted_to_org_id ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent-ink">
            <TagIcon className="size-3" /> {v.restricted_to_org_name ?? "Bespoke"}
          </span>
        ) : (
          <span className="text-sm text-muted">Public</span>
        ),
    },
    {
      key: "price",
      header: "Price",
      render: (v) => {
        const flat = v.prices.find((p) => p.kind === "flat");
        const extras = v.prices.filter((p) => p.kind !== "flat");
        return (
          <div className="flex flex-col gap-1">
            <span className="money font-semibold text-ink">{flat ? priceLabel(flat) : "—"}</span>
            {extras.length > 0 ? (
              <span className="flex flex-wrap gap-1">
                {extras.map((p) => (
                  <Tag key={p.price_id}>{priceLabel(p)}</Tag>
                ))}
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "published",
      header: "Published",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (v) => <span className="tnum text-muted">{v.published_at ? formatDate(v.published_at) : "—"}</span>,
    },
    {
      key: "subs",
      header: "Subscribers",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (v) => <span className="tnum text-ink-soft">{formatInt(v.subscriber_count)}</span>,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title={product.name} subtitle={product.description} />
      <DataTable
        columns={columns}
        rows={versions}
        rowKey={(v) => v.plan_version_id}
        caption={`${product.name} plan versions`}
        dense
        emptyState={
          <EmptyState
            title="No plan versions to show"
            description="This product has no version matching the filter. Turn on Show drafts to see unpublished ones."
          />
        }
      />
    </Card>
  );
}

const ALL_PRODUCTS = "all";
const NO_PRODUCTS: Product[] = [];

export function PlansPage() {
  const { can } = useOperatorAuth();
  const toast = useToast();
  const catalog = useAsync(getCatalog, []);
  const orgs = useAsync(() => listOrganizations(), []);
  const [productFilter, setProductFilter] = useState<string>(ALL_PRODUCTS);
  const [showDrafts, setShowDrafts] = useState(true);
  const [creating, setCreating] = useState(false);

  const products = useMemo(
    () => (catalog.status === "success" ? catalog.data.products : NO_PRODUCTS),
    [catalog],
  );
  const orgList = orgs.status === "success" ? orgs.data.items : [];

  const shownProducts = useMemo(
    () => (productFilter === ALL_PRODUCTS ? products : products.filter((p) => p.product_key === productFilter)),
    [products, productFilter],
  );

  function versionsFor(productKey: string): PlanVersion[] {
    if (catalog.status !== "success") return [];
    return catalog.data.versions
      .filter((v) => v.product_key === productKey && (showDrafts || v.state !== "draft"))
      .sort((a, b) => a.plan_name.localeCompare(b.plan_name) || b.version_no - a.version_no);
  }

  function handleDone(result: OverrideResult) {
    toast.success("Bespoke version created.", { description: acceptedDescription(result) });
    catalog.reload();
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Catalog"
        title="Plans & Pricing"
        description="The global, immutable catalog. Every published version is frozen — a new price is a new version."
        actions={
          <>
            <EnvBadge />
            {can("overrides") ? (
              <Button
                leadingIcon={<PlusIcon className="size-4" />}
                onClick={() => setCreating(true)}
                disabled={catalog.status !== "success" || orgs.status !== "success"}
              >
                Bespoke price
              </Button>
            ) : null}
          </>
        }
      />

      {catalog.status === "loading" ? (
        <div className="flex flex-col gap-5" aria-busy>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full rounded-tile" />
          ))}
        </div>
      ) : catalog.status === "error" ? (
        <Card className="p-6">
          <LoadFailure error={catalog.error} title="Couldn't load the catalog" onRetry={catalog.reload} />
        </Card>
      ) : products.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<TagIcon className="size-6" />}
            title="The catalog is empty"
            description="No product has a plan yet, so there is nothing to price."
          />
        </Card>
      ) : (
        <>
          <Card className="flex flex-wrap items-center gap-3 p-4 sm:p-5">
            <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1" role="group" aria-label="Product">
              <Pill active={productFilter === ALL_PRODUCTS} onClick={() => setProductFilter(ALL_PRODUCTS)}>
                All products
              </Pill>
              {products.map((p) => (
                <Pill
                  key={p.product_key}
                  active={productFilter === p.product_key}
                  onClick={() => setProductFilter(p.product_key)}
                >
                  {p.name}
                </Pill>
              ))}
            </div>
            <label className="ml-auto inline-flex items-center gap-2 text-sm font-semibold text-ink-soft">
              <input
                type="checkbox"
                checked={showDrafts}
                onChange={(e) => setShowDrafts(e.target.checked)}
                className="size-4 accent-accent"
              />
              Show drafts
            </label>
          </Card>

          <div className="flex flex-col gap-5">
            {shownProducts.map((product) => (
              <ProductSection key={product.product_key} product={product} versions={versionsFor(product.product_key)} />
            ))}
          </div>
        </>
      )}

      {creating && catalog.status === "success" ? (
        <BespokePriceDialog
          org={null}
          organizations={orgList}
          catalog={catalog.data}
          onClose={() => setCreating(false)}
          onDone={handleDone}
        />
      ) : null}
    </div>
  );
}
