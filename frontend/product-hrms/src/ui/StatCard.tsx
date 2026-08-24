import type { ReactNode } from "react";

import type { StatCardData } from "@/api/types";
import { formatDelta } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { MiniBarChart } from "./MiniBarChart";
import { SampleChip } from "./SampleChip";
import { ArrowUpRight } from "./icons";

interface StatCardProps {
  data: StatCardData;
  icon: ReactNode;
  /** Flag the figure as representative rather than live (renders a "Sample data" chip). */
  sample?: boolean;
}

/**
 * A dashboard stat: label + icon, a big tabular number with an inline mini chart, a delta,
 * and the PlanIQ corner arrow.
 */
export function StatCard({ data, icon, sample = false }: StatCardProps) {
  const positive = data.delta_pct >= 0;
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2.5">
          <span className="grid size-9 place-items-center rounded-full bg-ground text-ink-soft">
            {icon}
          </span>
          <span className="text-sm font-medium text-muted">{data.label}</span>
        </div>
        {sample ? (
          <SampleChip />
        ) : (
          <span className="grid size-8 place-items-center rounded-full bg-ground text-ink transition-colors group-hover:bg-mint">
            <ArrowUpRight className="size-4" />
          </span>
        )}
      </div>

      <div className="flex items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-[2.6rem] leading-none font-extrabold tracking-tight tnum">
            {data.value}
          </span>
          <span
            className={cn(
              "text-xs font-semibold",
              positive ? "text-present" : "text-danger",
            )}
          >
            {formatDelta(data.delta_pct)}
            <span className="text-faint font-medium"> vs last week</span>
          </span>
        </div>
        <MiniBarChart data={data.series} className="shrink-0" />
      </div>
    </Card>
  );
}
