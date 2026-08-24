import type { ComponentType, SVGProps } from "react";

import type { Ticket } from "@/api/types";
import { cn } from "@/lib/cn";
import { slaInfo } from "@/lib/sla";
import type { SlaState } from "@/lib/sla";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  PauseCircleIcon,
} from "./icons";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * SLA state, deliberately labelled: every state pairs a colour with an icon *and* a word, so an
 * agent can read "Breached" or "Due in 28m" without depending on the red/amber/green at all —
 * the frontend rule that state must never be conveyed by colour alone.
 */
const TONE: Record<SlaState, string> = {
  breached: "bg-danger-soft text-danger",
  due_soon: "bg-late-soft text-late",
  on_track: "bg-present-soft text-present",
  paused: "bg-ground text-muted",
  met: "bg-present-soft text-present",
  missed: "bg-danger-soft text-danger",
};

const ICON: Record<SlaState, IconType> = {
  breached: AlertTriangleIcon,
  due_soon: ClockIcon,
  on_track: ClockIcon,
  paused: PauseCircleIcon,
  met: CheckCircleIcon,
  missed: AlertTriangleIcon,
};

interface SlaPillProps {
  ticket: Ticket;
  /** Use the shorter label ("−2h", "28m") for dense table cells. */
  dense?: boolean;
  className?: string;
}

export function SlaPill({ ticket, dense = false, className }: SlaPillProps) {
  const info = slaInfo(ticket);
  const Icon = ICON[info.state];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full pl-1.5 pr-2.5 py-1 text-xs font-semibold whitespace-nowrap tnum",
        TONE[info.state],
        className,
      )}
      title={info.label}
    >
      <Icon className="size-3.5" aria-hidden />
      {dense ? info.shortLabel : info.label}
    </span>
  );
}
