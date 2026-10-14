import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Can } from "@opsgrids/client";
import { CalendarIcon, MailIcon, SendIcon } from "@opsgrids/ui";

import { countSegment, sendCampaign } from "@/api/client";
import type { CampaignOut } from "@/api/types";
import { formatNumber } from "@/lib/format";
import type { ResolvedLists } from "@/lib/refs";
import { explainSendFailure } from "@/lib/sendGate";
import type { GateField, GateNext, SendGateReason } from "@/lib/sendGate";
import { Button, Card, ConfirmDialog, InlineAlert, useAnnounce } from "@/ui";
import { CampaignScheduleDialog } from "./CampaignScheduleDialog";
import { CampaignTestSendDialog } from "./CampaignTestSendDialog";

/**
 * The send gate, from the customer's side.
 *
 * `POST /engage/campaigns/{id}/send` decides whether a campaign goes — nothing here does. This
 * panel's job is to make the decision *legible*: say plainly what pressing Send will do before it
 * happens, and when the service refuses, show its sentence verbatim with the one step that fixes
 * it (`lib/sendGate.ts`). A refusal is not an error state: nothing was sent, nothing is broken, and
 * the campaign is exactly the draft it was a moment ago.
 *
 * **Send acts on the saved draft, not on the form.** While the form has unsaved changes the
 * control is disabled and says why — pressing it would mail the version on the server, and a person
 * who has just fixed a typo in the subject is precisely the person who expects it to be in the mail.
 *
 * The audience is described from what this screen already holds. For a list it says how many
 * contacts are on it *now*; for everyone it does not invent a figure, because the service resolves
 * "everyone you may mail" (contacts minus suppressions) at expansion and this app cannot count it.
 */

/**
 * Says who the send goes to, claiming only what was learned. A list this screen could name is named
 * with its size today; one the service answered `404` for no longer exists; one that simply could
 * not be read (a role without the list read) is "the list chosen for this campaign" — never "not
 * found", which would be a claim about the service.
 */
function audienceSummary(
  campaign: CampaignOut,
  resolved: ResolvedLists,
  reach: number | null = null,
): string {
  if (campaign.audience === "segment") {
    const who =
      campaign.channel === "whatsapp" ? "who opted in to WhatsApp marketing" : "who may be emailed";
    return reach === null
      ? `the contacts in the segment chosen for this campaign ${who}`
      : `${formatNumber(reach)} ${reach === 1 ? "contact" : "contacts"} in the segment chosen for this campaign ${who}, as of now`;
  }
  if (campaign.audience === "all_sendable") {
    return "everyone you may mail — every contact except addresses on your Suppressions list";
  }
  const list = resolved.lists.find((l) => l.id === campaign.list_id);
  if (list !== undefined) {
    return `${list.name}, ${formatNumber(list.member_count)} ${list.member_count === 1 ? "contact" : "contacts"} today, minus any that are suppressed`;
  }
  if (campaign.list_id !== null && resolved.gone.has(campaign.list_id)) {
    return "a list that no longer exists";
  }
  return "the list chosen for this campaign";
}

const LINK_CLASS =
  "relative hit-44 inline-flex h-9 items-center rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** The one step that fixes a refusal: a field on this form, a screen in this app, or — for a plan
 *  fact — the dashboard on its own host, which is an `<a>` and never a router path. */
function GateAction({
  next,
  onFocusField,
}: {
  next: GateNext;
  onFocusField: (field: GateField) => void;
}) {
  if (next.kind === "field") {
    return (
      <Button variant="outline" size="sm" onClick={() => onFocusField(next.field)}>
        {next.label}
      </Button>
    );
  }
  if (next.external === true) {
    return (
      <a href={next.to} className={LINK_CLASS}>
        {next.label}
      </a>
    );
  }
  return (
    <Link to={next.to} className={LINK_CLASS}>
      {next.label}
    </Link>
  );
}

export function CampaignSendPanel({
  campaign,
  senderLabel,
  resolved,
  dirty,
  onSent,
  onScheduled,
  onFocusField,
}: {
  campaign: CampaignOut;
  /** "Name <address>", or a neutral phrase when the sender could not be named. */
  senderLabel: string;
  resolved: ResolvedLists;
  /** The form beside this has changes that are not saved. */
  dirty: boolean;
  onSent: (campaign: CampaignOut) => void;
  /** The service accepted a schedule: the campaign as it now holds it, and the sentence to
   *  announce. Scheduling moves the campaign off this panel entirely (draft → scheduled), so the
   *  caller reloads rather than holding the result here. */
  onScheduled: (campaign: CampaignOut, message: string) => void;
  onFocusField: (field: GateField) => void;
}) {
  const announce = useAnnounce();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [reason, setReason] = useState<SendGateReason | null>(null);
  const [testing, setTesting] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  /* The segment's reach on this channel, counted now; a segment re-evaluates when it sends. */
  const [reach, setReach] = useState<number | null>(null);
  const whatsapp = campaign.channel === "whatsapp";
  const via = whatsapp ? "on WhatsApp, as its approved template," : `as ${senderLabel}`;

  useEffect(() => {
    setReach(null);
    if (campaign.audience !== "segment" || campaign.segment_id === null) return;
    let live = true;
    countSegment(campaign.segment_id, campaign.channel).then(
      (out) => {
        if (live) setReach(out.recipients);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [campaign.audience, campaign.segment_id, campaign.channel]);

  async function send() {
    if (pending) return;
    setPending(true);
    try {
      const sent = await sendCampaign(campaign.id);
      setConfirming(false);
      setReason(null);
      onSent(sent);
    } catch (caught) {
      // The dialog closes: a refusal is a page-level fact with a next step, not a "try again"
      // inside a modal. The gate does not change its mind because the same request is repeated.
      setConfirming(false);
      setReason(explainSendFailure(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-base font-bold text-ink">Send</h2>

      <Can
        permission="engage.campaign.campaign.send"
        scope="ORG"
        fallback={
          <InlineAlert tone="info" live="off" title="You can draft this campaign, but not send it">
            Sending has its own permission, separate from drafting. Ask an Engage administrator to
            grant you “Send a campaign”, or ask them to send it.
          </InlineAlert>
        }
      >
        <p className="text-sm text-muted">
          Sends the saved draft {via} to {audienceSummary(campaign, resolved, reach)}.{" "}
          {whatsapp
            ? "The service checks the template is approved, WhatsApp is connected and every recipient's opt-in before it lets anything go."
            : "The service checks the sender, its domain, your provider connection and the unsubscribe link before it lets anything go."}
        </p>

        <div aria-live="polite" className="empty:hidden">
          {reason !== null ? (
            <InlineAlert
              tone={reason.tone}
              title={reason.title}
              action={
                reason.next === null ? undefined : (
                  <GateAction next={reason.next} onFocusField={onFocusField} />
                )
              }
            >
              <p>{reason.message}</p>
              {reason.hint !== null ? <p className="mt-1">{reason.hint}</p> : null}
              <p className="mt-1">That request sent nothing.</p>
            </InlineAlert>
          ) : null}
        </div>

        <div className="flex flex-wrap items-start gap-2">
          <Button
            onClick={() => {
              setReason(null);
              setConfirming(true);
            }}
            disabled={dirty || pending}
            leadingIcon={<SendIcon className="size-4" />}
          >
            Send campaign
          </Button>
          {whatsapp ? null : (
            <Button
              variant="outline"
              onClick={() => setTesting(true)}
              disabled={dirty}
              leadingIcon={<MailIcon className="size-4" />}
            >
              Send a preview
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => setScheduling(true)}
            disabled={dirty}
            leadingIcon={<CalendarIcon className="size-4" />}
          >
            Schedule
          </Button>
          {dirty ? (
            <span className="w-full text-xs text-muted">
              Save your changes first — sending (and scheduling) uses the saved draft, not what is
              on screen.
            </span>
          ) : null}
        </div>
      </Can>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void send()}
        tone="primary"
        title="Send this campaign now?"
        body={`“${campaign.name}” goes out ${via} to ${audienceSummary(campaign, resolved, reach)}. You can cancel it while it is sending, but a message that has already left cannot be recalled.`}
        confirmLabel="Send campaign"
        pendingLabel="Sending…"
        pending={pending}
      />

      <CampaignTestSendDialog
        open={testing}
        campaignId={campaign.id}
        campaignName={campaign.name}
        onClose={() => setTesting(false)}
        onSent={(message) => {
          setTesting(false);
          announce(message);
        }}
      />

      <CampaignScheduleDialog
        open={scheduling}
        campaign={campaign}
        onClose={() => setScheduling(false)}
        onScheduled={(updated, message) => {
          setScheduling(false);
          onScheduled(updated, message);
        }}
      />
    </Card>
  );
}
