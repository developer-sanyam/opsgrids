import { Link } from "react-router-dom";

import type { Incident } from "@/api/types";
import { formatSlaClock } from "@/lib/format";
import { EmptyState } from "./States";
import { PriorityPill, SlaPill } from "./StatusPill";
import { ChevronRight, ShieldCheckIcon } from "./icons";

/**
 * The escalation queue: open incidents that are P1 or already breaching SLA, each linking to its
 * detail. When it is empty, that is good news — the empty state says so rather than looking broken.
 */
export function NeedsAttention({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) {
    return (
      <EmptyState
        icon={<ShieldCheckIcon className="size-6" />}
        title="Nothing on fire"
        description="No P1 incidents and no SLA breaches right now. The desk is on top of the queue."
      />
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {incidents.map((incident) => (
        <li key={incident.id}>
          <Link
            to={`/incidents/${incident.id}`}
            className="group flex items-center gap-3 rounded-tile px-3 py-3 transition-colors hover:bg-ground focus-visible:bg-ground"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <PriorityPill priority={incident.priority} />
                <SlaPill state={incident.sla_state} />
              </div>
              <p className="truncate text-sm font-semibold text-ink">{incident.summary}</p>
              <p className="truncate text-xs text-muted">
                <span className="tnum">{incident.id}</span> · {incident.assignee} ·{" "}
                <span className="tnum">{formatSlaClock(incident.sla_minutes_remaining)}</span>
              </p>
            </div>
            <ChevronRight className="size-4 shrink-0 text-faint transition-colors group-hover:text-ink" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
