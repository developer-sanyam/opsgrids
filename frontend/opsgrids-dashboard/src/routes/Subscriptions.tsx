import { useEffect, useState } from "react";

import { listProducts, listSubscriptions } from "@/api";
import type { Product, Subscription } from "@/api/types";
import { daysUntil, formatDate } from "@/lib/money";
import { Card, PageHeader } from "@/ui";

type Load =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; subscriptions: Subscription[]; names: Record<string, string> };

/** What the organisation has started, and where each one stands. Read-only in this slice. */
export function SubscriptionsPage() {
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [subscriptions, products] = await Promise.all([listSubscriptions(), listProducts().catch(() => [] as Product[])]);
        if (cancelled) return;
        const names = Object.fromEntries(products.map((p) => [p.key, p.name]));
        setLoad({ state: "ready", subscriptions, names });
      } catch (err) {
        if (cancelled) return;
        setLoad({ state: "error", message: err instanceof Error ? err.message : "Something went wrong." });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Subscriptions" description="Each product your organisation has started, its state, and when the trial ends." />

      {load.state === "loading" ? <p className="text-sm text-muted">Loading…</p> : null}
      {load.state === "error" ? (
        <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
          Couldn't load subscriptions. {load.message}
        </p>
      ) : null}

      {load.state === "ready" && load.subscriptions.length === 0 ? (
        <Card className="p-8">
          <p className="text-sm text-muted">Nothing started yet. Pick a product from the Products page to begin a 14-day trial.</p>
        </Card>
      ) : null}

      {load.state === "ready" && load.subscriptions.length > 0 ? (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted">
                <th className="px-5 py-3 font-semibold">Product</th>
                <th className="px-5 py-3 font-semibold">State</th>
                <th className="px-5 py-3 font-semibold">Trial ends</th>
                <th className="px-5 py-3 font-semibold">Current period</th>
              </tr>
            </thead>
            <tbody>
              {load.subscriptions.map((s) => {
                const days = daysUntil(s.trial_ends_at);
                return (
                  <tr key={s.id} className="border-t border-line">
                    <td className="px-5 py-3 font-semibold text-ink">{load.names[s.product_key] ?? s.product_key}</td>
                    <td className="px-5 py-3 text-ink-soft">{s.state}</td>
                    <td className="px-5 py-3 text-ink-soft">
                      {s.trial_ends_at ? `${formatDate(s.trial_ends_at)}${days !== null && days >= 0 ? ` (${days} days)` : ""}` : "—"}
                    </td>
                    <td className="px-5 py-3 text-ink-soft">
                      {s.current_period_start ? `${formatDate(s.current_period_start)} – ${formatDate(s.current_period_end)}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
