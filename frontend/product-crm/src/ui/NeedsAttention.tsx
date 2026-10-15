import { Link } from "react-router-dom";

import type { AttentionItem } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyName } from "@/data/lookups";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckSquareIcon,
  ClockIcon,
  EmptyState,
  cn,
  formatMoney,
} from "@opsgrids/ui";
import { OwnerBadge } from "./OwnerBadge";

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
export function NeedsAttention({
  items,
  openDealCount,
}: {
  items: AttentionItem[];
  /** How many open deals there are at all: with none, "nothing needs a nudge" would be a claim
   *  about a pipeline that does not exist yet. */
  openDealCount: number;
}) {
  const { companies } = useDirectory();
  if (items.length === 0) {
    return openDealCount === 0 ? (
      <EmptyState
        icon={<CheckSquareIcon className="size-6" />}
        title="No open deals yet"
        description="Add your first deal. Deals that are overdue, closing this week or going stale will be listed here."
      />
    ) : (
      <EmptyState
        icon={<CheckSquareIcon className="size-6" />}
        title="Nothing needs a nudge"
        description="None of your open deals are overdue, closing this week or going stale."
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
                <span className="truncate text-sm font-bold text-ink" title={deal.name}>{deal.name}</span>
                <span className="truncate text-xs text-muted" title={companyName(companies, deal.company_id)}>
                  {companyName(companies, deal.company_id)}
                </span>
              </div>
              <div className="hidden shrink-0 sm:block">
                <OwnerBadge ownerId={deal.owner_id} compact />
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1 leading-tight">
                <span className="tnum text-sm font-bold text-ink">
                  {formatMoney(deal.value_minor, deal.currency, { compact: true })}
                </span>
                <span className={cn("rounded-full px-2 py-0.5 text-xs font-semibold", meta.tone)}>
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
