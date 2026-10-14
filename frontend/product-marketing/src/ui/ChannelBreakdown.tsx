import { Link } from "react-router-dom";

import type { ChannelPerformance } from "@/api/types";
import { channelLabel, formatMoneyCompact, formatNumber, formatRoas } from "@/lib/format";
import { ArrowRightIcon } from "@opsgrids/ui";
import { CHANNEL_COLOR, CHANNEL_ICON } from "./channelMeta";

interface ChannelBreakdownProps {
  data: ChannelPerformance[];
  currency: string;
}

/**
 * Revenue by channel as horizontal bars, each channel keeping its fixed colour and icon. The bar
 * length is the channel's share of the largest channel's revenue, with spend, leads and ROAS read
 * out alongside — so a high-revenue channel that is also expensive is visible at a glance.
 */
export function ChannelBreakdown({ data, currency }: ChannelBreakdownProps) {
  const maxRevenue = Math.max(1, ...data.map((d) => d.revenue_minor));

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-4">
        {data.map((row) => {
          const Icon = CHANNEL_ICON[row.channel];
          const color = CHANNEL_COLOR[row.channel];
          const width = Math.max((row.revenue_minor / maxRevenue) * 100, 3);
          return (
            <li key={row.channel} className="flex flex-col gap-2">
              <div className="flex items-center gap-2.5">
                <span
                  className="grid size-7 shrink-0 place-items-center rounded-tile"
                  style={{ backgroundColor: color, color: "var(--color-surface)" }}
                >
                  <Icon className="size-4" />
                </span>
                <span className="font-semibold text-ink">{channelLabel(row.channel)}</span>
                <span className="ml-auto tnum font-bold text-ink">
                  {formatMoneyCompact(row.revenue_minor, currency)}
                </span>
                <span
                  className="w-14 shrink-0 rounded-full py-0.5 text-center text-xs font-bold tnum"
                  style={{ backgroundColor: color, color: "var(--color-surface)" }}
                >
                  {formatRoas(row.roas)}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${width}%`, backgroundColor: color }}
                />
              </div>
              <div className="flex items-center gap-3 text-xs text-muted">
                <span className="tnum">
                  {formatMoneyCompact(row.spend_minor, currency)} spent
                </span>
                <span aria-hidden className="text-faint">
                  ·
                </span>
                <span className="tnum">{formatNumber(row.leads)} leads</span>
                <span aria-hidden className="text-faint">
                  ·
                </span>
                <span className="tnum">{row.campaigns} campaigns</span>
              </div>
            </li>
          );
        })}
      </ul>

      <Link
        to="/channels"
        className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full border border-line bg-surface text-sm font-semibold text-ink hover:bg-ground transition-colors"
      >
        Channel detail
        <ArrowRightIcon className="size-4" />
      </Link>
    </div>
  );
}
