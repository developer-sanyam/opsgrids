import type { Priority, TicketStatus } from "@/api/types";
import { cn } from "@/lib/cn";
import { priorityLabel, statusLabel } from "@/lib/format";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold whitespace-nowrap";

/**
 * Priority, mapped to the semantic pill families:
 * Urgent = red, High = amber, Normal = blue, Low = grey. A dot backs the colour so the meaning
 * never rests on hue alone, and the word is always present.
 */
const PRIORITY_TONE: Record<Priority, string> = {
  urgent: "bg-danger-soft text-danger",
  high: "bg-late-soft text-late",
  normal: "bg-info-soft text-info",
  low: "bg-ground text-muted",
};

export function PriorityPill({ priority }: { priority: Priority }) {
  return (
    <span className={cn(PILL_BASE, PRIORITY_TONE[priority])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {priorityLabel(priority)}
    </span>
  );
}

/**
 * Ticket lifecycle status:
 * New = cyan, Open = blue, Pending = amber, On Hold = grey, Solved = green.
 */
const STATUS_TONE: Record<TicketStatus, string> = {
  new: "bg-cyan-soft text-cyan",
  open: "bg-info-soft text-info",
  pending: "bg-late-soft text-late",
  on_hold: "bg-ground text-muted",
  solved: "bg-present-soft text-present",
};

export function TicketStatusPill({ status }: { status: TicketStatus }) {
  return (
    <span className={cn(PILL_BASE, STATUS_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {statusLabel(status)}
    </span>
  );
}
