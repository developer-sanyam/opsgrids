import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, getPlatformHost, PLATFORM_PRODUCTS } from "@opsgrids/client";
import type { PlatformProductKey } from "@opsgrids/client";
import { EmptyState, ErrorState, SearchInput, Skeleton } from "@opsgrids/ui";

import { listActivations, listPlans, listProducts, listSubscriptions, startTrial } from "@/api";
import type { Activation, Plan, PlanVersion, Product, Subscription } from "@/api/types";
import { bundleMembers, bundlesIncluding, joinNames, productNames } from "@/lib/bundles";
import { daysUntil, formatDate } from "@/lib/money";
import { Button, Card, PageHeader } from "@/ui";
import { shortProductName } from "../lib/productName";

/** Every key `@opsgrids/client`'s platform catalogue answers a host for — `crm`'s `sales` alias
 *  and `operations`'s `inventory` alias never appear as a product `key` here, so they need no
 *  entry of their own. */
const PLATFORM_PRODUCT_KEYS = new Set<string>(PLATFORM_PRODUCTS.map((product) => product.key));

/**
 * Where each product's own app lives (ADR-0001's "one hostname per product", ADR-0013 §4),
 * env-configurable via `configurePlatformHosts` (dev and staging never link to production). A
 * product with no entry has no console on this deployment yet, and the card says so rather than
 * linking into a 404.
 */
function productHost(key: string): string | undefined {
  return PLATFORM_PRODUCT_KEYS.has(key) ? getPlatformHost(key as PlatformProductKey) : undefined;
}

/** The contact address for pricing, agreed per organisation rather than shown here (the owner's call). */
const PRICING_CONTACT_EMAIL = "info@opsgrids.com";

/**
 * The plan version a trial pins to: in the first plan with a published, flat-priced version, the
 * **newest public monthly** one — the catalogue's current list price. Publishing a new price
 * leaves the old version published (subscriptions stay pinned to it), so "the first one" would
 * start every new trial on a price the catalogue has moved past; a yearly version is its own
 * cadence and is never what a trial starts on. A plan with no public monthly version falls back
 * to its first published flat one. The API never tells this app what the price is — pricing is
 * agreed per client — but this version is still what `startTrial` needs and still what decides
 * whether a product can be started at all.
 */
function trialOffer(plans: Plan[]): PlanVersion | null {
  for (const plan of plans) {
    const flat = plan.versions.filter((v) => v.state === "published" && v.prices.some((p) => p.kind === "flat"));
    const first = flat[0];
    if (!first) continue;
    const monthly = flat.filter((v) => v.billing_interval === "month" && v.restricted_to_org_id === null);
    return monthly.reduce<PlanVersion>((newest, v) => (v.version_no > newest.version_no ? v : newest), monthly[0] ?? first);
  }
  return null;
}

interface Row {
  product: Product;
  plans: Plan[];
  subscription: Subscription | null;
  activation: Activation | null;
  /** The bundles this product is included in, by the name the customer bought them under. */
  includedIn: string[];
  /** For a bundle: each published rung, named, with its members' names. */
  rungs: { id: string; name: string; members: string[] }[];
}

/** A bundle subscription's name: its plan's ("Suite Trio") when the plans are loaded. */
function bundleLabel(subscription: Subscription, products: Product[], plansByKey: Map<string, Plan[]>): string {
  for (const plan of plansByKey.get(subscription.product_key) ?? []) {
    if (plan.versions.some((v) => v.id === subscription.plan_version_id)) return plan.name;
  }
  return productNames([subscription.product_key], products)[0] ?? subscription.product_key;
}

function rungsOf(product: Product, plans: Plan[], products: Product[]): Row["rungs"] {
  if (!product.is_bundle) return [];
  const out: Row["rungs"] = [];
  for (const plan of plans) {
    for (const version of plan.versions) {
      const members = bundleMembers(version);
      if (version.state !== "published" || version.restricted_to_org_id !== null || members.length === 0) continue;
      out.push({ id: version.id, name: plan.name, members: productNames(members, products) });
    }
  }
  return out;
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
      const plansByKey = new Map(sellable.map((p, i) => [p.key, plans[i] ?? []]));
      const rows: Row[] = sellable.map((product, i) => ({
        product,
        plans: plans[i] ?? [],
        subscription: subscriptions.find((s) => s.product_key === product.key && s.state !== "cancelled") ?? null,
        activation: activations.find((a) => a.product_key === product.key) ?? null,
        includedIn: bundlesIncluding(product.key, subscriptions).map((s) => bundleLabel(s, products, plansByKey)),
        rungs: rungsOf(product, plans[i] ?? [], products),
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
    const offer = trialOffer(row.plans);
    if (!offer) return;
    setBusy(row.product.key);
    setNotice(null);
    try {
      await startTrial(row.product.key, offer.id);
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
  const offer = trialOffer(row.plans);
  const host = productHost(product.key);
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

      {row.rungs.length > 0 ? (
        <ul className="flex list-none flex-col gap-1 p-0 text-sm">
          {row.rungs.map((rung) => (
            <li key={rung.id}>
              <span className="font-semibold text-ink">{rung.name}</span>{" "}
              <span className="text-muted">— {joinNames(rung.members)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {product.status !== "available" ? (
        <p className="text-sm font-semibold text-ink-soft">Coming soon</p>
      ) : offer ? (
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-ink-soft">Free 14-day trial</p>
          <p className="text-xs text-muted">
            Pricing is agreed with OpsGrids for your organisation — contact {PRICING_CONTACT_EMAIL}.{" "}
            <a
              href={`mailto:${PRICING_CONTACT_EMAIL}`}
              className="font-medium text-accent-ink underline-offset-2 hover:underline"
            >
              Email us
            </a>
          </p>
        </div>
      ) : (
        <p className="text-sm font-semibold text-ink-soft">Not available to start yet</p>
      )}

      <div className="mt-auto flex flex-col gap-2">
        {row.includedIn.length > 0 ? (
          <p className="text-sm font-medium text-ink-soft">Included in {joinNames(row.includedIn)}</p>
        ) : null}

        {product.is_bundle && subscription ? (
          <p className="text-xs text-muted">Each product it includes opens from its own card.</p>
        ) : null}

        {!subscription && row.includedIn.length > 0 ? (
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
        ) : null}

        {subscription ? (
          <p className="text-sm text-muted">
            {subscription.state === "trial" && trialDays !== null
              ? `On trial — ${trialDays > 0 ? `${trialDays} days left` : "ends today"} (${formatDate(subscription.trial_ends_at)})`
              : `Subscription: ${subscription.state}`}
          </p>
        ) : null}

        {product.is_bundle ? null : subscription ? (
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
        ) : product.status === "available" && offer && row.includedIn.length === 0 ? (
          <Button type="button" disabled={busy} onClick={onStart}>
            {busy ? "Starting…" : "Start 14-day trial"}
          </Button>
        ) : null}
      </div>
      </Card>
    </li>
  );
}
