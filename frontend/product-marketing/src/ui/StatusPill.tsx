import type { CampaignStatus } from "@/api/types";
import { campaignStatusLabel } from "@/lib/format";
import { cn } from "@opsgrids/ui";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/**
 * Campaign lifecycle status, mapped to the semantic pill families and always backed by a coloured
 * dot so the state never rests on colour alone:
 * Live = green, Paused = amber, Ended = blue, Draft = grey.
 */
const TONE: Record<CampaignStatus, string> = {
  live: "bg-present-soft text-present",
  paused: "bg-late-soft text-late",
  ended: "bg-info-soft text-info",
  draft: "bg-ground text-muted",
};

export function CampaignStatusPill({ status }: { status: CampaignStatus }) {
  return (
    <span className={cn(PILL_BASE, TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {campaignStatusLabel(status)}
    </span>
  );
}
