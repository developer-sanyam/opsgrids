import type { Priority, ProjectStatus } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";

/**
 * Project health status. Every word (`on_track`, `at_risk`, `blocked`, `done`) is registered, so
 * the registry's tone applies as-is — `done` (a delivered project) moves onto the registry's
 * success tone rather than PulseBoard's old info blue. `done` keeps PulseBoard's own wording
 * ("Delivered"); the rest use the registry's default label, which already matches.
 */
export function ProjectStatusPill({ status }: { status: ProjectStatus }) {
  return <StatusBadge status={status} label={status === "done" ? "Delivered" : undefined} />;
}

/**
 * Task priority. `high`, `medium` and `low` are all registered words — `medium` and `low` move
 * off PulseBoard's old info/grey onto the registry's amber/blue, matching Support and the other
 * apps that show the same words.
 */
export function PriorityPill({ priority }: { priority: Priority }) {
  return <StatusBadge status={priority} />;
}
