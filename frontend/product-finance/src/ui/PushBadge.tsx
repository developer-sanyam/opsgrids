import type { ImsPush } from "@/api/types";
import { driftSentence, pushView } from "@/data/compliance";
import { Tag } from "@opsgrids/ui";

interface PushBadgeProps {
  push: ImsPush | null | undefined;
  /**
   * How much to say. `compact` (the queue) names the state and, only where a person has to look at
   * it, the reason; `full` (a decision's history line) always says what it means.
   */
  detail?: "compact" | "full";
}

/**
 * Where the delivery of one decision to GSTN stands — and whether GSTN has since disagreed with
 * it — in words, with the colour only agreeing.
 *
 * Two independent facts, so two badges: the push's own state (`Queued to send`, `On GSTN`, `Send
 * failed`, …) and `GSTN differs`, which is true only of a decision that *was* sent and that the
 * last sync found the portal no longer showing. A pushed decision that drifted reads as both, and
 * a queued one never reads as drift — it is known not to match, which is not the same thing.
 */
export function PushBadge({ push, detail = "compact" }: PushBadgeProps) {
  const view = pushView(push);
  if (view === null || push === null || push === undefined) return null;
  const drifted = push.drift;
  const showDetail = view.detail !== null && view.detail !== "" && (detail === "full" || view.needsPerson);

  return (
    <div className="flex flex-col items-end gap-1 text-right">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        <Tag className={view.tone}>{view.label}</Tag>
        {drifted ? <Tag className="bg-late-soft text-late">GSTN differs</Tag> : null}
      </div>
      {showDetail ? <p className="max-w-[16rem] text-xs text-muted">{view.detail}</p> : null}
      {drifted ? <p className="max-w-[16rem] text-xs text-muted">{driftSentence(push)}</p> : null}
    </div>
  );
}
