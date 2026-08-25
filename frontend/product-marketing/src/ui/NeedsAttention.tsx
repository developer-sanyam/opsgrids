import { Link } from "react-router-dom";

import type { AttentionItem } from "@/data/derive";
import { formatMoneyCompact } from "@/lib/format";
import { AlertTriangleIcon, ArrowRightIcon, EmptyState } from "@atmora/ui";
import { ChannelBadge } from "./ChannelBadge";

/**
 * Underperforming campaigns that warrant a look: each row names the campaign, its channel and the
 * specific reason it surfaced, and links straight to the campaign so the fix is one click away.
 */
export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon={<AlertTriangleIcon className="size-6" />}
        title="Nothing needs attention"
        description="Every live campaign is clearing the 2× ROAS floor. Healthy spend across the board."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {items.map(({ campaign, reason }) => (
        <li key={campaign.id}>
          <Link
            to={`/campaigns/${campaign.id}`}
            className="group flex items-center gap-3 rounded-tile border border-line bg-surface px-3 py-3 hover:border-accent/40 hover:bg-ground/60 transition-colors"
          >
            <span
              className="grid size-9 shrink-0 place-items-center rounded-tile bg-negative-soft text-negative"
              aria-hidden
            >
              <AlertTriangleIcon className="size-5" />
            </span>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-semibold text-ink">{campaign.name}</span>
              <span className="truncate text-xs text-muted">{reason}</span>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="hidden sm:block">
                <ChannelBadge channel={campaign.channel} />
              </span>
              <span className="tnum text-xs text-faint">
                {formatMoneyCompact(campaign.spent_minor, campaign.currency)}
              </span>
              <ArrowRightIcon className="size-4 text-faint transition-colors group-hover:text-accent" />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
