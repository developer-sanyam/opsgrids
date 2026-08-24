import { useMemo, useState } from "react";

import { listMilestones } from "@/api/client";
import { NOW } from "@/api/fixtures";
import type { MilestoneOut, MilestoneStatus } from "@/api/types";
import { MILESTONE_STATUS_LABEL } from "@/data/labels";
import { formatDate, relativeDue } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Avatar,
  Card,
  EmptyState,
  ErrorState,
  MilestoneStatusPill,
  PageHeader,
  Pill,
  ProgressBar,
  Skeleton,
} from "@/ui";
import { FlagIcon } from "@/ui/icons";

const NO_MILESTONES: MilestoneOut[] = [];

type StatusFilter = MilestoneStatus | "all" | "attention";

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "attention", label: "Needs attention" },
  { value: "due_soon", label: MILESTONE_STATUS_LABEL.due_soon },
  { value: "on_track", label: MILESTONE_STATUS_LABEL.on_track },
  { value: "at_risk", label: MILESTONE_STATUS_LABEL.at_risk },
  { value: "slipping", label: MILESTONE_STATUS_LABEL.slipping },
  { value: "completed", label: MILESTONE_STATUS_LABEL.completed },
];

const ATTENTION: ReadonlySet<MilestoneStatus> = new Set<MilestoneStatus>(["slipping", "at_risk"]);

function MilestoneRow({ milestone }: { milestone: MilestoneOut }) {
  const d = new Date(`${milestone.due_date}T00:00:00Z`);
  const day = d.toLocaleDateString("en-IN", { day: "2-digit", timeZone: "UTC" });
  const month = d.toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" });
  const overdue = milestone.status === "slipping";

  return (
    <li className="flex items-center gap-4 rounded-tile px-3 py-3 hover:bg-ground/60 transition-colors">
      <div
        className={
          "grid size-12 shrink-0 place-items-center rounded-tile text-center leading-none " +
          (overdue ? "bg-danger-soft text-danger" : "bg-ground text-ink-soft")
        }
      >
        <span className="text-base font-extrabold tnum">{day}</span>
        <span className="text-[0.6rem] font-bold uppercase tracking-wide">{month}</span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-sm font-bold text-ink">{milestone.title}</span>
          <span className="truncate text-xs text-muted tnum">
            {milestone.project_code} · {milestone.project_name}
          </span>
        </div>
        <ProgressBar
          value={milestone.progress_pct}
          className="max-w-xs"
          {...(milestone.status === "slipping" || milestone.status === "at_risk"
            ? { health: milestone.status === "slipping" ? ("delayed" as const) : ("at_risk" as const) }
            : {})}
        />
      </div>

      <div className="hidden shrink-0 items-center gap-2 sm:flex">
        <Avatar name={milestone.owner.name} size="sm" />
        <div className="flex flex-col leading-tight">
          <span className="text-xs font-semibold text-ink">{milestone.owner.name}</span>
          <span className="text-[0.7rem] text-muted">{milestone.owner.role}</span>
        </div>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        <MilestoneStatusPill status={milestone.status} />
        <span className="text-[0.7rem] font-medium text-faint tnum">
          {formatDate(milestone.due_date)} · {relativeDue(milestone.due_date, NOW)}
        </span>
      </div>
    </li>
  );
}

export function MilestonesPage() {
  const milestonesState = useAsync(listMilestones, []);
  const [filter, setFilter] = useState<StatusFilter>("all");

  const items = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data.items : NO_MILESTONES),
    [milestonesState],
  );

  const rows = useMemo(() => {
    const filtered = items.filter((m) => {
      if (filter === "all") return true;
      if (filter === "attention") return ATTENTION.has(m.status);
      return m.status === filter;
    });
    // Unfinished first, ordered by due date; completed sink to the bottom.
    return filtered.sort((a, b) => {
      const aDone = a.status === "completed" ? 1 : 0;
      const bDone = b.status === "completed" ? 1 : 0;
      if (aDone !== bDone) return aDone - bDone;
      return a.due_date.localeCompare(b.due_date);
    });
  }, [items, filter]);

  const attentionCount = useMemo(
    () => items.filter((m) => ATTENTION.has(m.status)).length,
    [items],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Delivery"
        title="Milestones"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <FlagIcon className="size-4 text-muted" />
            <span className="tnum">{attentionCount}</span>
            <span className="text-muted font-medium">need attention</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {FILTERS.map((f) => (
            <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>

        {milestonesState.status === "loading" ? (
          <div className="flex flex-col gap-2 py-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-tile" />
            ))}
          </div>
        ) : milestonesState.status === "error" ? (
          <ErrorState message={milestonesState.error.message} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FlagIcon className="size-6" />}
            title="No milestones here"
            description="No milestones match this filter. Switch back to All to see the full list."
          />
        ) : (
          <ul className="flex flex-col divide-y divide-line/70">
            {rows.map((m) => (
              <MilestoneRow key={m.id} milestone={m} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
