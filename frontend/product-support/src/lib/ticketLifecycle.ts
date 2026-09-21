/**
 * The ticket lifecycle state machine, mirrored from `services/support/app/domain.py`'s
 * `ALLOWED_TRANSITIONS` — the server is the one place a transition is actually enforced
 * (`POST /support/tickets/{id}/status` answers an illegal one with 409), but the Status control
 * only offers a caller the moves the server would accept, rather than a free-form set that fails
 * half the time.
 */
import type { TicketStatus } from "@/api/types";

export const ALLOWED_TRANSITIONS: Record<TicketStatus, TicketStatus[]> = {
  new: ["open", "pending", "solved"],
  open: ["pending", "on_hold", "solved"],
  pending: ["open", "on_hold", "solved"],
  on_hold: ["open", "solved"],
  solved: ["open"],
};

export function canTransition(current: TicketStatus, target: TicketStatus): boolean {
  return ALLOWED_TRANSITIONS[current].includes(target);
}
