import { Link } from "react-router-dom";

import type { AttentionItem } from "@/api/types";
import { cn } from "@/lib/cn";
import { AlertTriangleIcon, ArrowRightIcon, CoinsIcon, ClockIcon, OctagonIcon } from "./icons";

const SEVERITY: Record<
  AttentionItem["severity"],
  { label: string; tone: string; icon: typeof AlertTriangleIcon }
> = {
  blocked: { label: "Blocked", tone: "bg-danger-soft text-danger", icon: OctagonIcon },
  over_budget: { label: "Over budget", tone: "bg-danger-soft text-danger", icon: CoinsIcon },
  overdue: { label: "Overdue", tone: "bg-late-soft text-late", icon: ClockIcon },
  at_risk: { label: "At risk", tone: "bg-late-soft text-late", icon: AlertTriangleIcon },
};

/**
 * The "needs attention" list: the projects worst-off first, each with a severity chip, the reason
 * it surfaced, and a jump into the project list filtered to it. Empty when the portfolio is calm.
 */
export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  return (
    <ul className="flex flex-col divide-y divide-line">
      {items.map((item) => {
        const meta = SEVERITY[item.severity];
        const Icon = meta.icon;
        return (
          <li key={item.project_id}>
            <Link
              to={`/projects?q=${encodeURIComponent(item.code)}`}
              className="group flex items-start gap-3 py-3 transition-colors hover:bg-ground/50 -mx-2 px-2 rounded-tile"
            >
              <span className={cn("mt-0.5 grid size-8 shrink-0 place-items-center rounded-tile", meta.tone)}>
                <Icon className="size-4" />
              </span>
              <div className="flex min-w-0 flex-1 flex-col leading-snug">
                <div className="flex items-center gap-2">
                  <span className="truncate font-semibold text-ink">{item.name}</span>
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 text-[0.65rem] font-bold uppercase tracking-wide",
                      meta.tone,
                    )}
                  >
                    {meta.label}
                  </span>
                </div>
                <span className="truncate text-xs text-muted">
                  {item.code} · {item.client}
                </span>
                <span className="mt-0.5 text-sm text-ink-soft">{item.reason}</span>
              </div>
              <ArrowRightIcon className="mt-1 size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
