import { useEffect, useState } from "react";

import { Button, Card, DataTable, EmptyState, ErrorState, FilterBar, StatusBadge } from "@opsgrids/ui";
import type { Column, FilterSelect } from "@opsgrids/ui";

import { listPlans } from "@/api/catalog";
import type { Audience, PlanListItemOut, PlanState } from "@/api/catalog";
import { KNOWN_PRODUCT_KEYS, productLabel } from "@/lib/labels";
import { INTERVAL_LABEL, priceLabel } from "./planFormat";

const PAGE_SIZE = 25;

const AUDIENCE_OPTIONS = [
  { value: "restricted", label: "Bespoke & partner" },
  { value: "all", label: "Every audience" },
];

const STATE_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "retired", label: "Retired" },
];

export interface PlanFilters {
  productKey: string;
  /** "" is the default public audience. */
  audience: "" | Exclude<Audience, "public">;
  state: "" | PlanState;
}

interface PlanListProps {
  filters: PlanFilters;
  onFiltersChange: (next: PlanFilters) => void;
  onOpen: (plan: PlanListItemOut) => void;
  /** Bumped by the page after a write, to reload from the first page. */
  reloadToken: number;
}

function CurrentPrice({ plan }: { plan: PlanListItemOut }) {
  const current = plan.current;
  if (current === null) return <span className="text-sm text-faint">Nothing published</span>;
  const [first, ...rest] = current.prices;
  return (
    <div className="flex flex-col items-end gap-1">
      <span className="mono text-sm font-semibold text-ink">{first ? priceLabel(first) : "—"}</span>
      <span className="text-xs text-muted">
        v{current.version_no} · {INTERVAL_LABEL[current.billing_interval] ?? current.billing_interval}
        {rest.length > 0 ? ` · +${rest.length} more` : ""}
      </span>
    </div>
  );
}

function VersionsCell({ plan }: { plan: PlanListItemOut }) {
  const { counts } = plan;
  const parts = [
    `${counts.published} published`,
    counts.draft > 0 ? `${counts.draft} draft` : null,
    counts.grandfathered > 0 ? `${counts.grandfathered} withdrawn` : null,
    counts.bespoke > 0 ? `${counts.bespoke} bespoke` : null,
  ].filter((part): part is string => part !== null);
  return <span className="text-sm text-muted">{parts.join(" · ")}</span>;
}

/**
 * The catalogue's plans, a keyset page at a time — public audience by default, because bespoke
 * versions multiply on the public flat plans and the default view should stay bounded.
 */
export function PlanList({ filters, onFiltersChange, onOpen, reloadToken }: PlanListProps) {
  const [rows, setRows] = useState<PlanListItemOut[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string>("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const query = {
    productKey: filters.productKey || undefined,
    audience: (filters.audience || "public") as Audience,
    state: filters.state || undefined,
    limit: PAGE_SIZE,
  };

  useEffect(() => {
    let active = true;
    setStatus("loading");
    listPlans(query)
      .then((page) => {
        if (!active) return;
        setRows(page.items);
        setCursor(page.next_cursor);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Plans could not be loaded.");
        setStatus("error");
      });
    return () => {
      active = false;
    };
    // The query is derived from `filters`; listing it would re-run on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.productKey, filters.audience, filters.state, reloadToken, attempt]);

  async function loadMore() {
    if (cursor === null) return;
    setLoadingMore(true);
    try {
      const page = await listPlans({ ...query, cursor });
      setRows((current) => [...current, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "More plans could not be loaded.");
      setStatus("error");
    } finally {
      setLoadingMore(false);
    }
  }

  const selects: FilterSelect[] = [
    {
      key: "product",
      label: "Product",
      allLabel: "All products",
      options: KNOWN_PRODUCT_KEYS.map((key) => ({ value: key, label: productLabel(key) })),
      value: filters.productKey,
      onChange: (value) => onFiltersChange({ ...filters, productKey: value }),
    },
    {
      key: "audience",
      label: "Audience",
      allLabel: "Public",
      options: AUDIENCE_OPTIONS,
      value: filters.audience,
      onChange: (value) => onFiltersChange({ ...filters, audience: value as PlanFilters["audience"] }),
    },
    {
      key: "state",
      label: "State",
      allLabel: "Any state",
      options: STATE_OPTIONS,
      value: filters.state,
      onChange: (value) => onFiltersChange({ ...filters, state: value as PlanFilters["state"] }),
    },
  ];

  const columns: Column<PlanListItemOut>[] = [
    {
      key: "plan",
      header: "Plan",
      mobile: "title",
      render: (plan) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-semibold text-ink">{plan.name}</span>
          <span className="mono truncate text-xs text-muted">{plan.code}</span>
        </div>
      ),
    },
    { key: "product", header: "Product", render: (plan) => <span className="text-sm">{productLabel(plan.product_key)}</span> },
    { key: "state", header: "State", mobile: "status", render: (plan) => <StatusBadge status={plan.state} /> },
    { key: "price", header: "Current price", align: "right", render: (plan) => <CurrentPrice plan={plan} /> },
    { key: "versions", header: "Versions", render: (plan) => <VersionsCell plan={plan} /> },
    {
      key: "open",
      header: <span className="sr-only">Open</span>,
      align: "right",
      mobile: "actions",
      render: (plan) => (
        <Button variant="secondary" size="sm" onClick={() => onOpen(plan)} aria-label={`Open ${plan.name}`}>
          Open
        </Button>
      ),
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <FilterBar
        label="Filter plans"
        filters={selects}
        onClearAll={() => onFiltersChange({ productKey: "", audience: "", state: "" })}
      />
      <p className="sr-only" role="status" aria-live="polite">
        {status === "ready" ? `Showing ${rows.length} plan${rows.length === 1 ? "" : "s"}${cursor ? ", more available" : ""}.` : ""}
      </p>
      <DataTable
        caption="Plans"
        columns={columns}
        rows={status === "ready" || loadingMore ? rows : []}
        rowKey={(plan) => plan.id}
        loading={status === "loading"}
        mobileCard="auto"
        errorState={
          status === "error" ? (
            <ErrorState title="Plans could not be loaded" message={error} onRetry={() => setAttempt((n) => n + 1)} />
          ) : undefined
        }
        emptyState={
          <EmptyState
            title="No plans match"
            description={
              filters.productKey || filters.audience || filters.state
                ? "Clear a filter to see more of the catalogue."
                : "Create a plan, then draft and publish a version to put a price on sale."
            }
          />
        }
      />
      {cursor !== null && status === "ready" ? (
        <div>
          <Button variant="secondary" loading={loadingMore} onClick={() => void loadMore()}>
            Load more plans
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
