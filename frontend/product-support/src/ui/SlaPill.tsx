import type { Ticket } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";
import { slaInfo } from "@/lib/sla";

interface SlaPillProps {
  ticket: Ticket;
  /** Use the shorter label ("−2h", "28m") for dense table cells. */
  dense?: boolean;
  className?: string;
}

/**
 * SLA state, deliberately labelled — the badge's word ("Breached", "Due in 28m") carries the
 * meaning, never the tone alone. Every state but `missed` is a registered word; `missed` takes an
 * explicit tone. Missing from the registry: `missed`.
 */
export function SlaPill({ ticket, dense = false, className }: SlaPillProps) {
  const info = slaInfo(ticket);
  const label = dense ? info.shortLabel : info.label;
  if (info.state === "missed") return <StatusBadge tone="danger" label={label} className={className} />;
  return <StatusBadge status={info.state} label={label} className={className} />;
}
