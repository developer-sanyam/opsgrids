/**
 * The incident lifecycle state machine, mirrored from `services/itsm/app/domain.py`'s
 * `INCIDENT_TRANSITIONS` — the server is the one place a transition is actually enforced
 * (`POST /itsm/incidents/{number}/status` answers an illegal one with 409), but the status
 * control only offers a caller the moves the server would accept, rather than a free-form set
 * that fails half the time.
 */
import type { IncidentStatus } from "@/api/types";

export const INCIDENT_TRANSITIONS: Record<IncidentStatus, IncidentStatus[]> = {
  new: ["in_progress", "on_hold", "resolved"],
  in_progress: ["on_hold", "resolved"],
  on_hold: ["in_progress", "resolved"],
  resolved: ["closed", "in_progress"],
  closed: [],
};

/** The verb for one legal move, in context — "Reopen" from `resolved` reads differently from
 *  "Start work" from `new`, though both land on `in_progress`. */
export function transitionLabel(current: IncidentStatus, target: IncidentStatus): string {
  if (target === "resolved") return "Resolve…";
  if (target === "closed") return "Close";
  if (target === "in_progress") {
    if (current === "resolved") return "Reopen";
    if (current === "on_hold") return "Resume";
    return "Start work";
  }
  if (target === "on_hold") return "Put on hold";
  return target;
}
