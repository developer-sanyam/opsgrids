import type { UsageSummary } from "@/api/types";
import { formatInt } from "@/lib/format";
import { Card, SectionHeader, Tag } from "@/ui";

/** Current-period usage, metered per product. Empty says so; it is not a card that vanishes. */
export function UsageCard({ usage }: { usage: UsageSummary[] }) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Usage" subtitle="Current period, metered per product" />
      {usage.length === 0 ? (
        <p className="py-4 text-sm text-muted">No metered usage recorded for this account.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {usage.map((u) => (
            <li key={u.meter_key} className="flex flex-wrap items-center gap-3 py-3">
              <div className="flex min-w-0 flex-col leading-tight">
                <span className="font-semibold text-ink">{u.meter_name}</span>
                <span className="money text-xs text-faint">{u.meter_key}</span>
              </div>
              <div className="ml-auto flex items-center gap-3">
                {u.is_billable ? (
                  <span className="rounded-full bg-cyan-soft px-2 py-0.5 text-xs font-semibold text-cyan">
                    Billable
                  </span>
                ) : (
                  <Tag>Not billed</Tag>
                )}
                <span className="tnum text-right text-sm font-semibold text-ink">
                  {formatInt(u.quantity)} <span className="font-normal text-muted">{u.unit}</span>
                  {u.included !== null ? (
                    <span className="block text-xs font-normal text-faint">
                      of {formatInt(u.included)} included
                    </span>
                  ) : null}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
