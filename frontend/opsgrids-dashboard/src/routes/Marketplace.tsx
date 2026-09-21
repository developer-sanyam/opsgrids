import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import { EmptyState, ErrorState, SearchInput, Skeleton } from "@opsgrids/ui";

import { listActivations, listPlans, listProducts, listSubscriptions, startTrial } from "@/api";
import type { Activation, Plan, PlanVersion, Price, Product, Subscription } from "@/api/types";
import { daysUntil, formatDate, formatMoney, intervalLabel } from "@/lib/money";
import { Button, Card, PageHeader } from "@/ui";
import { shortProductName } from "../lib/productName";

/**
 * Where each product's own app lives (ADR-0001's "one hostname per product", ADR-0013 §4). A
 * product with no entry has no console on this deployment yet, and the card says so rather than
 * linking into a 404.
 */
const PRODUCT_HOSTS: Record<string, string> = {
  operations: "https://ops.opsgrids.com/",
  hrms: "https://people.opsgrids.com/",
  crm: "https://revenue.opsgrids.com/",
  pulseboard: "https://pulse.opsgrids.com/",
  projects: "https://projects.opsgrids.com/",
  support: "https://support.opsgrids.com/",
  marketing: "https://marketing.opsgrids.com/",
  finance: "https://finance.opsgrids.com/",
  itsm: "https://it.opsgrids.com/",
  storefront: "https://store.opsgrids.com/",
  engage: "https://engage.opsgrids.com/",
};

/** The one price a card shows: the first published version's flat price, if there is one. */
function flatPrice(plans: Plan[]): { version: PlanVersion; price: Price } | null {
  for (const plan of plans) {
    for (const version of plan.versions) {
      if (version.state !== "published") continue;
      const price = version.prices.find((p) => p.kind === "flat" && p.amount_minor !== null);
      if (price) return { version, price };
    }
  }
  return null;
}

interface Row {
  product: Product;
  plans: Plan[];
  subscription: Subscription | null;
  activation: Activation | null;
}

type Load =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; rows: Row[] };

export function MarketplacePage() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";

  const refresh = useCallback(async () => {
    setLoad({ state: "loading" });
    try {
      const [products, activations, subscriptions] = await Promise.all([
        listProducts(),
        listActivations().catch(() => [] as Activation[]),
        listSubscriptions().catch(() => [] as Subscription[]),
      ]);
      const sellable = products.filter((p) => !p.is_platform);
      const plans = await Promise.all(
        sellable.map((p) => (p.status === "available" ? listPlans(p.key).catch(() => [] as Plan[]) : Promise.resolve([] as Plan[]))),
      );
      const rows: Row[] = sellable.map((product, i) => ({
        product,
        plans: plans[i] ?? [],
        subscription: subscriptions.find((s) => s.product_key === product.key && s.state !== "cancelled") ?? null,
        activation: activations.find((a) => a.product_key === product.key) ?? null,
      }));
      setLoad({ state: "ready", rows });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      setLoad({ state: "error", message });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleStart(row: Row) {
    const offer = flatPrice(row.plans);
    if (!offer) return;
    setBusy(row.product.key);
    setNotice(null);
    try {
      await startTrial(row.product.key, offer.version.id);
      setNotice(`${shortProductName(row.product.name)} is starting. Your 14-day trial begins now; its roles arrive in a moment.`);
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setNotice(`Couldn't start ${shortProductName(row.product.name)}: ${err.problem?.detail ?? err.message}`);
      } else {
        setNotice(`Couldn't start ${shortProductName(row.product.name)}.`);
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Products"
        title="Start what your organisation needs"
        description="Every product runs on the same account, the same people and the same permissions. Start one on a 14-day trial; add another whenever you like."
      />

      {notice ? (
        <p role="status" className="rounded-tile bg-surface px-4 py-3 text-sm font-medium text-ink-soft" style={{ boxShadow: "var(--shadow-float)" }}>
          {notice}
        </p>
      ) : null}

      {load.state === "loading" ? (
        <ul className="grid list-none gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3" aria-hidden>
          {[0, 1, 2].map((i) => (
            <li key={i}>
              <Skeleton className="h-56 w-full rounded-card" />
            </li>
          ))}
        </ul>
      ) : null}
      {load.state === "error" ? (
        <ErrorState
          title="Couldn't load the catalogue"
          message={load.message}
          onRetry={() => void refresh()}
        />
      ) : null}

      {load.state === "ready" ? (
        <SearchInput
          label="Search products"
          placeholder="Search by name, tagline or category"
          value={query}
          onChange={(e) => {
            const next = e.target.value;
            setSearchParams(next ? { q: next } : {}, { replace: true });
          }}
          containerClassName="max-w-sm"
        />
      ) : null}

      {load.state === "ready" ? (
        (() => {
          const needle = query.trim().toLowerCase();
          const filtered = needle
            ? load.rows.filter((row) =>
                [row.product.name, row.product.tagline, row.product.category]
                  .filter((v): v is string => v !== null)
                  .some((v) => v.toLowerCase().includes(needle)),
              )
            : load.rows;
          if (filtered.length === 0) {
            return (
              <EmptyState
                title="No products match that search"
                description="Try a shorter search term."
                action={
                  <Button variant="outline" onClick={() => setSearchParams({}, { replace: true })}>
                    Clear search
                  </Button>
                }
              />
            );
          }
          return (
            <ul className="grid list-none gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {filtered.map((row) => (
                <ProductCard key={row.product.key} row={row} busy={busy === row.product.key} onStart={() => void handleStart(row)} />
              ))}
            </ul>
          );
        })()
      ) : null}
    </div>
  );
}

function ProductCard({ row, busy, onStart }: { row: Row; busy: boolean; onStart: () => void }) {
  const { product, subscription, activation } = row;
  const offer = flatPrice(row.plans);
  const host = PRODUCT_HOSTS[product.key];
  const entitled = activation?.entitles === true;
  const trialDays = daysUntil(subscription?.trial_ends_at ?? null);

  return (
    <li className="flex">
      <Card className="flex w-full flex-col gap-4 p-6">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">{product.category ?? "Product"}</p>
        <h2 className="display text-xl font-extrabold">{shortProductName(product.name)}</h2>
        {product.tagline ? <p className="text-sm text-muted">{product.tagline}</p> : null}
      </div>

      <p className="text-sm font-semibold text-ink-soft">
        {product.status !== "available" ? (
          "Coming soon"
        ) : offer ? (
          <>
            {formatMoney(offer.price.amount_minor ?? 0, offer.price.currency)}{" "}
            <span className="font-normal text-muted">{intervalLabel(offer.version.billing_interval)}, flat</span>
          </>
        ) : (
          "Price on request"
        )}
      </p>

      <div className="mt-auto flex flex-col gap-2">
        {subscription ? (
          <p className="text-sm text-muted">
            {subscription.state === "trial" && trialDays !== null
              ? `On trial — ${trialDays > 0 ? `${trialDays} days left` : "ends today"} (${formatDate(subscription.trial_ends_at)})`
              : `Subscription: ${subscription.state}`}
          </p>
        ) : null}

        {subscription ? (
          host && entitled ? (
            <a
              href={host}
              className="inline-flex h-11 items-center justify-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
            >
              Open {shortProductName(product.name)}
            </a>
          ) : (
            <p className="text-xs text-muted">
              {entitled ? "Its console is not on this deployment yet." : "Getting ready — refresh in a moment."}
            </p>
          )
        ) : product.status === "available" && offer ? (
          <Button type="button" disabled={busy} onClick={onStart}>
            {busy ? "Starting…" : "Start 14-day trial"}
          </Button>
        ) : null}
      </div>
      </Card>
    </li>
  );
}
