import { Link } from "react-router-dom";

import { NOW } from "@/api/fixtures";
import type { MilestoneOut } from "@/api/types";
import { formatDateShort, relativeDue } from "@/lib/format";
import { Avatar, CheckSquareIcon, EmptyState } from "@atmora/ui";
import { MilestoneStatusPill } from "./ProjectPills";

/**
 * The "needs attention" list: milestones that are slipping or at risk, soonest first. Each row
 * names the milestone and its project, its owner, and how overdue or imminent it is. An empty list
 * is a good outcome, so it says so rather than showing nothing.
 */
export function NeedsAttention({ milestones }: { milestones: MilestoneOut[] }) {
  if (milestones.length === 0) {
    return (
      <EmptyState
        icon={<CheckSquareIcon className="size-6" />}
        title="Nothing slipping"
        description="Every milestone is on track or already delivered. Enjoy the calm."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-1">
      {milestones.map((m) => {
        const overdue = relativeDue(m.due_date, NOW);
        return (
          <li key={m.id}>
            <Link
              to="/milestones"
              className="flex items-center gap-3 rounded-tile px-2 py-2.5 hover:bg-ground transition-colors"
            >
              <Avatar name={m.owner.name} size="sm" />
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="truncate text-sm font-semibold text-ink">{m.title}</span>
                <span className="truncate text-xs text-muted">
                  <span className="tnum">{m.project_code}</span> · {m.project_name}
                </span>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <MilestoneStatusPill status={m.status} />
                <span className="text-[0.7rem] font-medium text-faint tnum">
                  {formatDateShort(m.due_date)} · {overdue}
                </span>
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
