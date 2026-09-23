import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Can } from "@opsgrids/client";

import { cancelCampaign, getCampaign, resumeCampaign } from "@/api/client";
import type { CampaignOut, CampaignStats } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { campaignStatusWord, formatInstant, formatNumber } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { Button, CampaignStatusTag, Card, ConfirmDialog, InlineAlert } from "@/ui";
import { CampaignPreview } from "./CampaignPreview";
import { CampaignRecipients } from "./CampaignRecipients";

/**
 * A campaign once it has left `draft`: what it said, who it went to, and how it is going.
 *
 * **The counts are exactly the five the service folds** (`CampaignStats`: recipients, queued, sent,
 * failed, suppressed), from one grouped query over `campaign_recipients`. There is no delivered,
 * bounced or unsubscribed figure on the wire and none is invented here: a recipient row records
 * that the message was handed to the provider, and has no link to the delivery ledger or to a later
 * unsubscribe. The note under the counts says so, and points at Suppressions, where a bounce or an
 * unsubscribe does land — as a fact about the address, not a count against a campaign.
 *
 * While the campaign is `sending` — or `paused`, which the driver retries on its own — the counts
 * and the recipients table poll. A poll that fails leaves the last figures up and "Last updated"
 * says how old they are; a poll the service *refuses* (a `401`/`403`) stops for good, keeps the
 * figures, and says so — the counts never revert to zero while a line above them claims they are
 * still refreshing. When the status moves on, `onChanged` reloads the page so it remounts in its
 * settled form.
 */

/**
 * Told when a campaign's status moved and the page must be re-read. `automatic` is for a change the
 * *service* made — a poll that found the campaign sent, or resumed by the driver — as opposed to one
 * the person asked for (Send, Cancel, Resume): the first must not take their focus, because they
 * did nothing and may be anywhere on the page.
 */
export type StatusChanged = (message: string, options?: { automatic?: boolean }) => void;

const LIVE_INTERVAL_MS = 5_000;
const CANCELLABLE = new Set(["scheduled", "sending", "paused"]);
const LIVE_STATUSES = new Set(["sending", "paused"]);

function Stat({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-bold uppercase tracking-wider text-faint">{label}</dt>
      <dd className="text-2xl font-extrabold tabular-nums text-ink">{formatNumber(value)}</dd>
      {note !== undefined ? <dd className="text-xs text-muted">{note}</dd> : null}
    </div>
  );
}

function Counts({ stats }: { stats: CampaignStats }) {
  const processed = stats.sent + stats.failed + stats.suppressed;
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <Stat label="Recipients" value={stats.recipients} />
        <Stat label="Queued" value={stats.queued} note="Not sent yet" />
        <Stat label="Sent" value={stats.sent} note="Handed to your provider" />
        <Stat label="Failed" value={stats.failed} note="Refused by the provider" />
        <Stat label="Suppressed" value={stats.suppressed} note="Skipped on purpose" />
      </dl>
      <p className="text-sm text-muted tabular-nums">
        {formatNumber(processed)} of {formatNumber(stats.recipients)} recipients dealt with.
      </p>
      <p className="text-xs text-muted">
        “Sent” means your provider accepted the message, not that it reached an inbox. Delivery,
        bounce and unsubscribe outcomes are not counted per campaign; addresses that bounce or
        unsubscribe appear on{" "}
        <Link
          to="/suppressions"
          className="font-semibold text-accent-ink underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Suppressions
        </Link>
        .
      </p>
    </div>
  );
}

function Timeline({ campaign }: { campaign: CampaignOut }) {
  const rows: { label: string; at: string }[] = [
    { label: "Drafted", at: campaign.created_at },
    ...(campaign.started_at === null ? [] : [{ label: "Started sending", at: campaign.started_at }]),
    ...(campaign.completed_at === null ? [] : [{ label: "Finished", at: campaign.completed_at }]),
    ...(campaign.cancelled_at === null ? [] : [{ label: "Cancelled", at: campaign.cancelled_at }]),
  ];
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="flex flex-col">
          <dt className="text-xs font-bold uppercase tracking-wider text-faint">{row.label}</dt>
          <dd className="text-ink">{formatInstant(row.at)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ReportView({
  campaign,
  senderLabel,
  audienceLabel,
  live,
  stopped,
  lastUpdated,
  onChanged,
}: {
  campaign: CampaignOut;
  senderLabel: string;
  audienceLabel: string;
  live: boolean;
  /** Live updates were refused and have stopped; the figures on screen are the last good ones. */
  stopped: boolean;
  lastUpdated: string | null;
  onChanged: StatusChanged;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const [cancelFailure, setCancelFailure] = useState<string | null>(null);
  const [resumePending, setResumePending] = useState(false);
  const [resumeFailure, setResumeFailure] = useState<string | null>(null);

  async function confirmCancel() {
    if (cancelPending) return;
    setCancelPending(true);
    setCancelFailure(null);
    try {
      await cancelCampaign(campaign.id);
      setCancelling(false);
      onChanged(`${campaign.name} cancelled. Recipients not yet sent will never be sent.`);
    } catch (caught) {
      setCancelFailure(failureMessage(caught));
    } finally {
      setCancelPending(false);
    }
  }

  async function resume() {
    if (resumePending) return;
    setResumePending(true);
    setResumeFailure(null);
    try {
      await resumeCampaign(campaign.id);
      onChanged(`${campaign.name} is sending again.`);
    } catch (caught) {
      setResumeFailure(failureMessage(caught));
    } finally {
      setResumePending(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {campaign.status === "paused" ? (
        <InlineAlert
          tone="danger"
          live="off"
          title="This campaign is paused and is not sending"
          action={
            <Can permission="engage.campaign.campaign.send" scope="ORG">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void resume()}
                disabled={resumePending}
              >
                {resumePending ? "Resuming…" : "Resume now"}
              </Button>
            </Can>
          }
        >
          <p>
            {campaign.pause_reason === ""
              ? "The send driver could not find a route to send on."
              : campaign.pause_reason}
          </p>
          <p className="mt-1">
            Fix the cause under{" "}
            <Link
              to="/connection"
              className="font-semibold text-accent-ink underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Connections
            </Link>{" "}
            or{" "}
            <Link
              to="/senders"
              className="font-semibold text-accent-ink underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Senders
            </Link>
            , then resume. The driver also retries by itself after a short wait.
          </p>
          {resumeFailure !== null ? (
            <p role="alert" className="mt-1 font-semibold">
              {resumeFailure}
            </p>
          ) : null}
        </InlineAlert>
      ) : null}

      <Card className="flex flex-col gap-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-base font-bold text-ink">Delivery</h2>
            <CampaignStatusTag status={campaign.status} />
          </div>
          <Can permission="engage.campaign.campaign.write" scope="ORG">
            {CANCELLABLE.has(campaign.status) ? (
              <Button
                variant="outline"
                size="sm"
                className="text-negative"
                onClick={() => setCancelling(true)}
              >
                Cancel campaign
              </Button>
            ) : null}
          </Can>
        </div>

        <Counts stats={campaign.stats} />
        <Timeline campaign={campaign} />

        {live && !stopped ? (
          <p className="text-xs text-muted">
            Counts refresh every few seconds while this campaign is sending or paused.
            {lastUpdated !== null ? ` Last updated ${lastUpdated}.` : ""}
          </p>
        ) : null}
        {live && stopped ? (
          <p role="status" className="text-xs font-semibold text-ink">
            Live updates stopped — reload to refresh.
            {lastUpdated !== null ? ` These figures are as of ${lastUpdated}.` : ""}
          </p>
        ) : null}
      </Card>

      <CampaignRecipients campaignId={campaign.id} live={live} />

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-base font-bold text-ink">What was sent</h2>
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-bold uppercase tracking-wider text-faint">Sent as</dt>
            <dd className="text-ink first-letter:uppercase">{senderLabel}</dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs font-bold uppercase tracking-wider text-faint">Addressed to</dt>
            <dd className="text-ink">{audienceLabel}</dd>
          </div>
        </dl>
        <CampaignPreview
          subject={campaign.subject}
          html={campaign.html_body}
          text={campaign.text_body}
          heading="h3"
        />
      </Card>

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          setCancelFailure(null);
        }}
        onConfirm={() => void confirmCancel()}
        title="Cancel this campaign?"
        body={`Recipients that have not been sent yet will never be sent. Messages that have already gone (${formatNumber(campaign.stats.sent)} so far) cannot be recalled. This can't be undone.`}
        confirmLabel="Cancel campaign"
        cancelLabel="Keep sending"
        pendingLabel="Cancelling…"
        pending={cancelPending}
        {...(cancelFailure !== null ? { error: cancelFailure } : {})}
      />
    </div>
  );
}

function LiveReport({
  initial,
  senderLabel,
  audienceLabel,
  onChanged,
}: {
  initial: CampaignOut;
  senderLabel: string;
  audienceLabel: string;
  onChanged: StatusChanged;
}) {
  const state = usePolling(() => getCampaign(initial.id), [initial.id], LIVE_INTERVAL_MS);
  const polled = state.status === "success" ? state.data : null;
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  useEffect(() => {
    if (polled !== null) setLastUpdated(new Date().toLocaleTimeString());
  }, [polled]);

  // The status moved on (sent, cancelled elsewhere, paused by the driver): remount in the form
  // that status is shown in, rather than keep polling something that has stopped changing.
  useEffect(() => {
    if (polled !== null && polled.status !== initial.status) {
      onChanged(`${polled.name} is now ${campaignStatusWord(polled.status)}.`, { automatic: true });
    }
  }, [polled, initial.status, onChanged]);

  return (
    <ReportView
      campaign={polled ?? initial}
      senderLabel={senderLabel}
      audienceLabel={audienceLabel}
      live
      stopped={state.stopped}
      lastUpdated={lastUpdated}
      onChanged={onChanged}
    />
  );
}

export function CampaignReport({
  campaign,
  senderLabel,
  audienceLabel,
  onChanged,
}: {
  campaign: CampaignOut;
  /** "Name <address>", or a neutral phrase when the sender could not be named. */
  senderLabel: string;
  /** A list's name, "Everyone you may mail", or a neutral phrase — see `lib/refs.ts`. */
  audienceLabel: string;
  onChanged: StatusChanged;
}) {
  return LIVE_STATUSES.has(campaign.status) ? (
    <LiveReport
      initial={campaign}
      senderLabel={senderLabel}
      audienceLabel={audienceLabel}
      onChanged={onChanged}
    />
  ) : (
    <ReportView
      campaign={campaign}
      senderLabel={senderLabel}
      audienceLabel={audienceLabel}
      live={false}
      stopped={false}
      lastUpdated={null}
      onChanged={onChanged}
    />
  );
}
