import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, getPlatformHost, PLATFORM_PRODUCTS } from "@opsgrids/client";
import type { PlatformProductKey } from "@opsgrids/client";
import { EmptyState, ErrorState, SearchInput, Skeleton, StatusBadge, useToast } from "@opsgrids/ui";

import { listActivations, listPlans, listProducts, listSubscriptionPlanSummaries, listSubscriptions, startTrial } from "@/api";
import type { Activation, Plan, PlanVersion, Product, Subscription, SubscriptionPlanSummary } from "@/api/types";
import { bundleMembers, bundlesIncluding, joinNames, productNames } from "@/lib/bundles";
import { daysUntil } from "@/lib/money";
import { billedPhrase, statusLine, trialTerms } from "@/lib/subscriptionCopy";
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
 * to its newest published flat one — never the oldest: the server refuses a superseded version
 * (`not_offerable`), so the oldest would only fail. The API never tells this app what the price
 * is — pricing is agreed per client — but this version is still what `startTrial` needs and still
 * what decides whether a product can be started at all.
 */
function trialOffer(plans: Plan[]): PlanVersion | null {
  const newest = (versions: PlanVersion[]): PlanVersion | undefined =>
    versions.reduce<PlanVersion | undefined>((best, v) => (best === undefined || v.version_no > best.version_no ? v : best), undefined);
  for (const plan of plans) {
    const flat = plan.versions.filter((v) => v.state === "published" && v.prices.some((p) => p.kind === "flat"));
    if (flat.length === 0) continue;
    const monthly = flat.filter((v) => v.billing_interval === "month" && v.restricted_to_org_id === null);
    return newest(monthly) ?? newest(flat) ?? null;
  }
  return null;
}

interface Row {
  product: Product;
  plans: Plan[];
  subscription: Subscription | null;
  summary: SubscriptionPlanSummary | null;
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
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";

  const refresh = useCallback(async () => {
    setLoad({ state: "loading" });
    try {
      const [products, activations, subscriptions, summaries] = await Promise.all([
        listProducts(),
        listActivations().catch(() => [] as Activation[]),
        listSubscriptions().catch(() => [] as Subscription[]),
        Promise.resolve(listSubscriptionPlanSummaries?.() ?? []).catch(() => [] as SubscriptionPlanSummary[]),
      ]);
      const sellable = products.filter((p) => !p.is_platform);
      const plans = await Promise.all(
        sellable.map((p) => (p.status === "available" ? listPlans(p.key).catch(() => [] as Plan[]) : Promise.resolve([] as Plan[]))),
      );
      const plansByKey = new Map(sellable.map((p, i) => [p.key, plans[i] ?? []]));
      const summariesBySubId = new Map(summaries.map((s) => [s.subscription_id, s]));
      const rows: Row[] = sellable.map((product, i) => {
        const sub = subscriptions.find((s) => s.product_key === product.key && s.state !== "cancelled") ?? null;
        return {
          product,
          plans: plans[i] ?? [],
          subscription: sub,
          summary: sub ? (summariesBySubId.get(sub.id) ?? null) : null,
          activation: activations.find((a) => a.product_key === product.key) ?? null,
          includedIn: bundlesIncluding(product.key, subscriptions).map((s) => bundleLabel(s, products, plansByKey)),
          rungs: rungsOf(product, plans[i] ?? [], products),
        };
      });
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
    try {
      const started = await startTrial(row.product.key, offer.id);
      // A toast, not a banner at the top of the page: the card that was pressed is often well below
      // the fold, and a toast is announced where a banner appearing off-screen is not.
      toast.success(
        `${shortProductName(row.product.name)} is starting. Your 14-day trial begins now; its roles arrive in a moment. ${trialTerms(started.trial_ends_at)}`,
      );
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(`Couldn't start ${shortProductName(row.product.name)}: ${err.problem?.detail ?? err.message}`);
      } else {
        toast.error(`Couldn't start ${shortProductName(row.product.name)}.`);
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

      {load.state === "loading" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton label="Loading products" className="h-56 w-full rounded-card" />
          <Skeleton className="h-56 w-full rounded-card max-sm:hidden" />
          <Skeleton className="h-56 w-full rounded-card max-xl:hidden" />
        </div>
      ) : null}
      {load.state === "error" ? (
        <ErrorState
          title="Couldn't load the catalogue"
          message={load.message}
          onRetry={() => void refresh()}
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
          return (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <SearchInput
                  label="Search products"
                  placeholder="Search by name, tagline or category"
                  value={query}
                  onChange={(e) => {
                    const next = e.target.value;
                    setSearchParams(next ? { q: next } : {}, { replace: true });
                  }}
                  containerClassName="w-full sm:max-w-sm"
                />
                {/* Announced as the search narrows the list - the count is the result a screen reader hears. */}
                <p aria-live="polite" className="tnum text-sm text-muted">
                  {filtered.length} {filtered.length === 1 ? "product" : "products"}
                </p>
              </div>
              {filtered.length === 0 ? (
                <EmptyState
                  title="No products match that search"
                  description="Try a shorter search term."
                  action={
                    <Button variant="outline" onClick={() => setSearchParams({}, { replace: true })}>
                      Clear search
                    </Button>
                  }
                />
              ) : (
                <ul className="grid grid-cols-1 list-none gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
                  {filtered.map((row) => (
                    <ProductCard key={row.product.key} row={row} busy={busy === row.product.key} onStart={() => void handleStart(row)} />
                  ))}
                </ul>
              )}
            </>
          );
        })()
      ) : null}
    </div>
  );
}

function ProductCard({ row, busy, onStart }: { row: Row; busy: boolean; onStart: () => void }) {
  const { product, subscription, activation, summary } = row;
  const offer = trialOffer(row.plans);
  const host = productHost(product.key);
  const entitled = activation?.entitles === true;
  const trialDays = daysUntil(subscription?.trial_ends_at ?? null);
  const isActive = subscription?.state === "active";
  const isTrial = subscription?.state === "trial";
  const isBundled = !subscription && row.includedIn.length > 0;

  const activePlanName =
    summary?.current?.plan_name ??
    row.plans.find((p) => p.versions.some((v) => v.id === subscription?.plan_version_id))?.name ??
    "Standard Plan";
  const cadence = billedPhrase(summary?.current?.billing_interval ?? "month");
  const renewal = subscription ? statusLine(subscription) : null;

  return (
    <li className="flex">
      <Card className="flex w-full flex-col gap-4 p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">{product.category ?? "Product"}</p>
            <h2 className="display text-xl font-extrabold text-ink">{shortProductName(product.name)}</h2>
          </div>
          {isActive ? (
            <StatusBadge status="active" />
          ) : isTrial ? (
            <StatusBadge tone="info" label={trialDays !== null && trialDays > 0 ? `Trial · ${trialDays}d left` : "Trial"} />
          ) : isBundled ? (
            <StatusBadge tone="success" label="Included" />
          ) : subscription?.state === "past_due" ? (
            <StatusBadge status="past_due" />
          ) : product.status !== "available" ? (
            <StatusBadge tone="neutral" label="Coming Soon" />
          ) : offer ? (
            <StatusBadge tone="neutral" label="Available" />
          ) : null}
        </div>

        {product.tagline ? <p className="text-sm text-muted">{product.tagline}</p> : null}

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

        {isActive ? (
          <div className="flex flex-col gap-1.5 rounded-tile border border-faint bg-ground/80 p-3.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wide text-muted">Current Plan</span>
              <span className="flex items-center gap-1.5 font-semibold text-positive">
                <span className="inline-block size-1.5 rounded-full bg-positive" /> Active Plan
              </span>
            </div>
            <p className="text-sm font-bold text-ink">{activePlanName}</p>
            <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
              <span>{cadence}</span>
              {renewal?.primary ? <span>· {renewal.primary}</span> : null}
            </div>
          </div>
        ) : isTrial ? (
          <div className="flex flex-col gap-2 rounded-tile border border-accent/20 bg-accent-soft/40 p-3.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold uppercase tracking-wide text-accent-ink">Free Trial</span>
              <span className="font-medium text-accent-ink">
                {trialDays !== null && trialDays > 0 ? `On trial — ${trialDays} days left` : "On trial — ends today"}
              </span>
            </div>
            <p className="text-xs text-muted">{trialTerms(subscription.trial_ends_at)}</p>
          </div>
        ) : isBundled ? (
          <div className="flex flex-col gap-1 rounded-tile border border-faint bg-ground/80 p-3.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">Included in Bundle</span>
            <p className="text-sm font-semibold text-ink">Included in {joinNames(row.includedIn)}</p>
            <p className="text-xs text-muted">Included with your organisation&apos;s active bundle.</p>
          </div>
        ) : product.status !== "available" ? (
          <p className="text-sm font-semibold text-ink-soft">Coming soon</p>
        ) : offer ? (
          <div className="flex flex-col gap-1">
            <p className="text-sm font-semibold text-ink-soft">Free 14-day trial</p>
            <p className="text-xs text-muted">{trialTerms()}</p>
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

        <div className="mt-auto flex flex-col gap-2 pt-2">
          {product.is_bundle && subscription ? (
            <p className="text-xs text-muted">Each product it includes opens from its own card.</p>
          ) : null}

          {product.is_bundle ? null : isActive || isTrial || isBundled ? (
            host && entitled ? (
              <a
                href={host}
                className="inline-flex h-11 items-center justify-center rounded-full bg-accent px-5 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
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
