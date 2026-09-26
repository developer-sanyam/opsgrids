import type { Priority, TicketStatus } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";

/**
 * Priority, on the shared tone registry: `urgent`, `high` and `low` are registered words, so they
 * take the registry's tone (`high` and `low` move off Support's old amber/grey onto the registry's
 * red/blue, matching how PulseBoard and the other apps show the same words). `normal` isn't
 * registered — Support's own word — so it keeps an explicit tone. Missing from the registry:
 * `normal`.
 */
export function PriorityPill({ priority }: { priority: Priority }) {
  if (priority === "normal") return <StatusBadge tone="info" label="Normal" />;
  return <StatusBadge status={priority} />;
}

/**
 * Ticket lifecycle status. Every word here is registered; `on_hold` keeps Support's capitalised
 * wording ("On Hold") instead of the registry's default "On hold".
 */
export function TicketStatusPill({ status }: { status: TicketStatus }) {
  return <StatusBadge status={status} label={status === "on_hold" ? "On Hold" : undefined} />;
}
