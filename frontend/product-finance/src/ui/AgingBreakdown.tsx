import type { AgingBucket } from "@/api/types";
import { cn } from "@opsgrids/ui";
import { formatMoney } from "@/lib/money";

interface AgingBreakdownProps {
  buckets: AgingBucket[];
}

/** Escalating tone: current is calm, older money runs hotter. */
const BAR_TONE: Record<string, string> = {
  current: "bg-present",
  d1_30: "bg-info",
  d31_60: "bg-late",
  d61_90: "bg-[#e0762a]",
  d90p: "bg-danger",
};

/**
 * Accounts-receivable aging: how much open receivable sits in each age band. Each row is a
 * proportional bar plus the exact amount and the number of invoices behind it. Bars are scaled to
 * the largest bucket so the shape reads at a glance; the amounts carry the precise figure.
 */
export function AgingBreakdown({ buckets }: AgingBreakdownProps) {
  const total = buckets.reduce((sum, b) => sum + b.amount_minor, 0);
  const max = Math.max(...buckets.map((b) => b.amount_minor), 1);

  return (
    <div className="flex flex-col gap-3.5">
      {buckets.map((bucket) => {
        const width = bucket.amount_minor === 0 ? 0 : (bucket.amount_minor / max) * 100;
        const share = total === 0 ? 0 : Math.round((bucket.amount_minor / total) * 100);
        return (
          <div key={bucket.key} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-semibold text-ink-soft">{bucket.label}</span>
              <span className="money text-sm font-bold text-ink">
                {formatMoney(bucket.amount_minor)}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div
                className="h-2 flex-1 overflow-hidden rounded-full bg-ground"
                role="img"
                aria-label={`${bucket.label}: ${formatMoney(bucket.amount_minor)}, ${share}% of receivables, ${bucket.count} invoices`}
              >
                <div
                  className={cn("h-full rounded-full transition-[width] duration-500", BAR_TONE[bucket.key] ?? "bg-accent")}
                  style={{ width: `${width}%` }}
                />
              </div>
              <span className="w-24 shrink-0 text-right text-xs text-faint tnum">
                {share}% · {bucket.count} {bucket.count === 1 ? "inv" : "invs"}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
