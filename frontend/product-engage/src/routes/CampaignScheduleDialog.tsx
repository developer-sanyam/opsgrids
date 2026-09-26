import { useEffect, useState } from "react";

import { DateTimeField, Select } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

import { scheduleCampaign } from "@/api/client";
import type { CampaignOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { IST_ZONE, canonicalZone, formatScheduledFor } from "@/lib/format";
import { Button, Dialog, InlineAlert } from "@/ui";

function detectZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || IST_ZONE;
  } catch {
    return IST_ZONE;
  }
}

/** The browser's own zone, plus India Standard Time when that is a different one — never a long
 *  list, because the person scheduling is thinking in one clock or the other, not a third. */
function zoneOptions(detected: string): SelectOption[] {
  const options: SelectOption[] = [
    { value: detected, label: `Your time zone — ${detected.replace(/_/g, " ")}` },
  ];
  if (canonicalZone(detected) !== canonicalZone(IST_ZONE)) {
    options.push({ value: IST_ZONE, label: "India Standard Time (IST)" });
  }
  return options;
}

/**
 * Schedule a draft to send later, or push back one already `scheduled` — the same
 * `POST /engage/campaigns/{id}/schedule` either way (`services.schedule_send`).
 *
 * Two steps, in one dialog: pick a wall-clock time in a zone, then confirm the instant it
 * resolves to before it is sent to the service. The timezone defaults to the browser's own —
 * the person scheduling is thinking in their own clock — with India Standard Time offered
 * alongside it. `scheduled_at` must be in the future; that is checked here before the service
 * ever sees the request, and shown verbatim, unchanged, if the service disagrees with anything
 * else about it (the same gate `sendCampaign` applies — no subject, an unusable sender, and so
 * on — since a scheduled send still has to be sendable when its moment comes).
 *
 * A failure lands back on the picker with what was chosen still in it — nothing here is thrown
 * away — and never on a page-level banner: this is one action on one campaign, not a page-wide
 * fact.
 */
export function CampaignScheduleDialog({
  open,
  campaign,
  onClose,
  onScheduled,
}: {
  open: boolean;
  campaign: CampaignOut;
  onClose: () => void;
  /** The service accepted it: the campaign as it now holds it, and the sentence to announce. */
  onScheduled: (campaign: CampaignOut, message: string) => void;
}) {
  const rescheduling = campaign.status === "scheduled";
  const detected = detectZone();

  const [step, setStep] = useState<"pick" | "confirm">("pick");
  const [zone, setZone] = useState(detected);
  const [instant, setInstant] = useState<string | null>(campaign.scheduled_at);
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // Seeded fresh every time the dialog opens: `Dialog` itself unmounts its children while closed,
  // but this component does not, so without this a second open would start from the last one's
  // leftovers rather than the campaign's current `scheduled_at`.
  useEffect(() => {
    if (!open) return;
    setStep("pick");
    setZone(detected);
    setInstant(campaign.scheduled_at);
    setFieldError(undefined);
    setFailure(null);
  }, [open, campaign.scheduled_at, detected]);

  function close() {
    if (pending) return;
    onClose();
  }

  function continueToConfirm() {
    if (instant === null) {
      setFieldError("Choose when this campaign should send.");
      return;
    }
    if (new Date(instant).getTime() <= Date.now()) {
      setFieldError("Choose a time in the future.");
      return;
    }
    setFieldError(undefined);
    setFailure(null);
    setStep("confirm");
  }

  async function confirm() {
    if (pending || instant === null) return;
    setPending(true);
    setFailure(null);
    try {
      const updated = await scheduleCampaign(campaign.id, { scheduled_at: instant });
      const when = formatScheduledFor(instant);
      const message = rescheduling
        ? `“${campaign.name}” is rescheduled for ${when}.`
        : `“${campaign.name}” is scheduled to send ${when}.`;
      onScheduled(updated, message);
    } catch (caught) {
      // Back to the picker, not stuck on the confirmation: the gate may be about the campaign
      // itself (no subject, an unusable sender), which this step's summary cannot fix.
      setStep("pick");
      setFailure(failureMessage(caught));
    } finally {
      setPending(false);
    }
  }

  const title = rescheduling ? "Reschedule this campaign" : "Schedule this campaign";
  const when = instant !== null ? formatScheduledFor(instant) : "";

  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      size="sm"
      {...(step === "pick"
        ? {
            description:
              "Pick when it should go out. Times are shown in whichever zone you choose below.",
          }
        : {})}
      footer={
        step === "pick" ? (
          <>
            <Button variant="ghost" onClick={close} disabled={pending}>
              Cancel
            </Button>
            <Button onClick={continueToConfirm} disabled={pending}>
              Continue
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={() => setStep("pick")} disabled={pending}>
              Back
            </Button>
            <Button onClick={() => void confirm()} disabled={pending}>
              {pending
                ? rescheduling
                  ? "Rescheduling…"
                  : "Scheduling…"
                : rescheduling
                  ? "Reschedule campaign"
                  : "Schedule campaign"}
            </Button>
          </>
        )
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert
            tone="danger"
            title={rescheduling ? "This wasn't rescheduled" : "This wasn't scheduled"}
            className="mb-4"
          >
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      {step === "pick" ? (
        <div className="flex flex-col gap-4">
          <Select
            label="Time zone"
            options={zoneOptions(detected)}
            value={zone}
            onChange={(event) => setZone(event.target.value)}
            disabled={pending}
          />
          <DateTimeField
            label="Send at"
            value={instant}
            onChange={(next) => {
              setInstant(next);
              if (fieldError !== undefined) setFieldError(undefined);
            }}
            zone={zone}
            min={new Date().toISOString()}
            disabled={pending}
            {...(fieldError !== undefined ? { error: fieldError } : {})}
          />
        </div>
      ) : (
        <p className="text-sm text-ink">
          {rescheduling
            ? `Move “${campaign.name}” to send at `
            : `Schedule “${campaign.name}” to send at `}
          <strong className="font-semibold">{when}</strong>
          {rescheduling ? " instead" : ""}? It sends on its own — nothing else about the campaign
          changes.
        </p>
      )}
    </Dialog>
  );
}
