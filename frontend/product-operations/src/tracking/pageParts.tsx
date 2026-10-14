import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { RequirePermission } from "@opsgrids/client";
import { Card, EmptyState, GoBackButton } from "@/ui";
import { ArrowRightIcon, ShieldIcon } from "@opsgrids/ui";

/** The screen's own answer to a missing grant, in place of an empty page. A UI gate, not
 *  protection: every endpoint behind these screens refuses the call on its own. */
export function NoAccess({ what }: { what: string }) {
  return (
    <Card className="p-6">
      <EmptyState
        icon={<ShieldIcon className="size-6" />}
        title={`You don't have access to ${what}`}
        description="Ask an administrator for the stock read permission, or reload if you were just given it."
        action={<GoBackButton />}
      />
    </Card>
  );
}

/** Wraps a screen in the stock-read gate, with the permission-denied state as its fallback. */
export function RequireStockRead({ what, children }: { what: string; children: ReactNode }) {
  return (
    <RequirePermission perm="operations.inventory.stock.read" scope="ORG" fallback={<NoAccess what={what} />}>
      {children}
    </RequirePermission>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      {children}
    </Link>
  );
}

export interface MetricItem {
  label: string;
  value: ReactNode;
}

/** The row of headline figures at the top of a detail screen. A description list, so a screen
 *  reader hears each label with its value. */
export function MetricGrid({ label, items }: { label: string; items: MetricItem[] }) {
  return (
    <dl aria-label={label} className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
          <dt className="text-xs font-medium text-muted">{item.label}</dt>
          <dd className="tnum text-lg font-extrabold text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
