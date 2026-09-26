import { StatusBadge } from "@opsgrids/ui";

import type { CampaignStatus } from "@/api/types";
import { campaignStatusLabel } from "@/lib/format";

/**
 * Campaign lifecycle status, on `StatusBadge` and the shared tone registry rather than this app's
 * own colours (see `opsgrids-ui/src/lib/status.ts`).
 *
 * `live`, `paused` and `draft` are registered words the registry already tones the way this app
 * always has. `ended` means something different here than the registry's general sense of the
 * word — a campaign that has ended is a plain fact, not the failure `ended` reads as elsewhere —
 * so it keeps its own tone instead of the registry's.
 */
export function CampaignStatusPill({ status }: { status: CampaignStatus }) {
  const label = campaignStatusLabel(status);
  if (status === "ended") return <StatusBadge tone="info" label={label} />;
  return <StatusBadge status={status} label={label} />;
}
