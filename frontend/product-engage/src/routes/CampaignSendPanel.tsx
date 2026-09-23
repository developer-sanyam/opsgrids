import { useState } from "react";
import { Link } from "react-router-dom";

import { Can } from "@opsgrids/client";
import { SendIcon } from "@opsgrids/ui";

import { sendCampaign } from "@/api/client";
import type { CampaignOut, ContactListOut, SenderOut } from "@/api/types";
import { formatNumber } from "@/lib/format";
import { explainSendFailure } from "@/lib/sendGate";
import type { GateField, GateNext, SendGateReason } from "@/lib/sendGate";
import { Button, Card, ConfirmDialog, InlineAlert } from "@/ui";

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

function audienceSummary(campaign: CampaignOut, lists: ContactListOut[]): string {
  if (campaign.audience === "all_sendable") {
    return "everyone you may mail — every contact except addresses on your Suppressions list";
  }
  const list = lists.find((l) => l.id === campaign.list_id);
  if (list === undefined) return "a list that could not be found";
  return `${list.name}, ${formatNumber(list.member_count)} ${list.member_count === 1 ? "contact" : "contacts"} today, minus any that are suppressed`;
}

const LINK_CLASS =
  "inline-flex h-9 items-center rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

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
  sender,
  lists,
  dirty,
  onSent,
  onFocusField,
}: {
  campaign: CampaignOut;
  sender: SenderOut | null;
  lists: ContactListOut[];
  /** The form beside this has changes that are not saved. */
  dirty: boolean;
  onSent: (campaign: CampaignOut) => void;
  onFocusField: (field: GateField) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [reason, setReason] = useState<SendGateReason | null>(null);

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

  const senderText = sender === null ? "the chosen sender" : `${sender.name} <${sender.address}>`;

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
          Sends the saved draft as {senderText} to {audienceSummary(campaign, lists)}. The service
          checks the sender, its domain, your provider connection and the unsubscribe link before it
          lets anything go.
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

        <div className="flex flex-col items-start gap-2">
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
          {dirty ? (
            <span className="text-xs text-muted">
              Save your changes first — sending uses the saved draft, not what is on screen.
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
        body={`“${campaign.name}” goes out as ${senderText} to ${audienceSummary(campaign, lists)}. You can cancel it while it is sending, but a message that has already left cannot be recalled.`}
        confirmLabel="Send campaign"
        pendingLabel="Sending…"
        pending={pending}
      />
    </Card>
  );
}
