import type { Priority, ProjectStatus } from "@/api/types";
import { cn } from "@/lib/cn";
import { PROJECT_STATUS_META } from "./statusMeta";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/** Project health status. A coloured dot backs up the colour so it never carries meaning alone. */
export function ProjectStatusPill({ status }: { status: ProjectStatus }) {
  const meta = PROJECT_STATUS_META[status];
  return (
    <span className={cn(PILL_BASE, meta.tone)}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {meta.label}
    </span>
  );
}

const PRIORITY_META: Record<Priority, { label: string; tone: string }> = {
  high: { label: "High", tone: "bg-danger-soft text-danger" },
  medium: { label: "Medium", tone: "bg-info-soft text-info" },
  low: { label: "Low", tone: "bg-ground text-muted" },
};

/** Task priority as a compact chip; the label carries meaning so colour is never the only cue. */
export function PriorityPill({ priority }: { priority: Priority }) {
  const meta = PRIORITY_META[priority];
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[0.7rem] font-bold", meta.tone)}>
      {meta.label}
    </span>
  );
}
