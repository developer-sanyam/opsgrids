import { Link } from "react-router-dom";

import type { AttentionItem } from "@/api/types";
import { companyName } from "@/data/lookups";
import { cn } from "@/lib/cn";
import { formatMoneyCompact } from "@/lib/format";
import { EmptyState } from "./States";
import { OwnerBadge } from "./OwnerBadge";
import { AlertTriangleIcon, ArrowRightIcon, CheckSquareIcon, ClockIcon } from "./icons";

const REASON_META: Record<
  AttentionItem["reason"],
  { label: string; tone: string; icon: (p: { className?: string }) => React.ReactNode }
> = {
  overdue: { label: "Overdue", tone: "bg-danger-soft text-danger", icon: AlertTriangleIcon },
  closing_soon: { label: "Closing soon", tone: "bg-late-soft text-late", icon: ClockIcon },
  stale: { label: "Going stale", tone: "bg-info-soft text-info", icon: ClockIcon },
};

/**
 * The Overview "needs attention" list: open deals that are overdue, closing within ten days, or
 * have gone quiet — each a link to the deal, with a reason chip and the value. Most urgent first.
 */
export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon={<CheckSquareIcon className="size-6" />}
        title="Nothing needs a nudge"
        description="No open deals are overdue, closing this week, or going stale. The pipeline is healthy."
      />
    );
  }

  return (
    <ul className="flex flex-col divide-y divide-line">
      {items.map(({ deal, reason, detail }) => {
        const meta = REASON_META[reason];
        const Icon = meta.icon;
        return (
          <li key={deal.id}>
            <Link
              to={`/deals/${deal.id}`}
              className="group flex items-center gap-3 py-3 -mx-2 px-2 rounded-tile hover:bg-ground/70 transition-colors"
            >
              <span className={cn("grid size-9 shrink-0 place-items-center rounded-full", meta.tone)}>
                <Icon className="size-[18px]" />
              </span>
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="truncate text-sm font-bold text-ink">{deal.name}</span>
                <span className="truncate text-xs text-muted">{companyName(deal.company_id)}</span>
              </div>
              <div className="hidden shrink-0 sm:block">
                <OwnerBadge ownerId={deal.owner_id} compact />
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1 leading-tight">
                <span className="tnum text-sm font-bold text-ink">
                  {formatMoneyCompact(deal.value_minor, deal.currency)}
                </span>
                <span className={cn("rounded-full px-2 py-0.5 text-[0.7rem] font-semibold", meta.tone)}>
                  {detail}
                </span>
              </div>
              <ArrowRightIcon className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-ink" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
