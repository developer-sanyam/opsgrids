import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { ArrowLeftIcon } from "@opsgrids/ui";

import { deleteCampaign, getCampaign, listDomains, listLists, listSenders } from "@/api/client";
import type { CampaignOut, ContactListOut, DomainOut, SenderOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { useRouteNotice } from "@/lib/useRouteNotice";
import {
  Announcer,
  Button,
  CampaignStatusTag,
  Card,
  ConfirmDialog,
  LoadFailure,
  PageHeader,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { CampaignForm } from "./CampaignForm";
import type { FocusRequest } from "./CampaignForm";
import { CampaignReport } from "./CampaignReport";
import { CampaignSendPanel } from "./CampaignSendPanel";

/**
 * The two campaign routes: `/campaigns/new` drafts one, `/campaigns/:campaignId` opens it.
 *
 * A campaign is one of two things to look at. While it is a `draft` it is a form — edited, tested,
 * sent or deleted. Once it has left `draft` it is a record — `CampaignReport` — and the service
 * refuses every edit (`services.update_campaign`), so nothing here offers one. Which of the two is
 * shown is the service's `status`, never a guess.
 *
 * The form's reference data — senders, their domains, and lists — is loaded with the campaign in
 * one `Promise.all`, so a screen either has everything it needs to render the pickers or shows one
 * failure. A read the caller may not make (a `403` on senders, say) surfaces here as the error it
 * is: the editor cannot be honest about a sender it was forbidden to see.
 */

const NO_SENDERS: SenderOut[] = [];
const NO_DOMAINS: DomainOut[] = [];
const NO_LISTS: ContactListOut[] = [];

function LoadingCard() {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-64 w-full rounded-tile" />
    </Card>
  );
}

function BackLink() {
  return (
    <Link
      to="/campaigns"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <ArrowLeftIcon className="size-4" />
      All campaigns
    </Link>
  );
}

/** A draft: the form, the send gate beside it, and delete. */
function DraftView({
  campaign,
  senders,
  domains,
  lists,
  onSaved,
  onChanged,
}: {
  campaign: CampaignOut;
  senders: SenderOut[];
  domains: DomainOut[];
  lists: ContactListOut[];
  /** The service accepted an edit: the campaign as it now holds it. No reload — the form keeps its
   *  focus and the page keeps its place. */
  onSaved: (campaign: CampaignOut, message: string) => void;
  /** The campaign's *status* moved (sent), so the page must be re-read and re-shown. */
  onChanged: (message: string) => void;
}) {
  const navigate = useNavigate();
  const [dirty, setDirty] = useState(false);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);

  const sender = senders.find((s) => s.id === campaign.sender_id) ?? null;

  async function confirmDelete() {
    if (deletePending) return;
    setDeletePending(true);
    setDeleteFailure(null);
    try {
      await deleteCampaign(campaign.id);
      setDeleting(false);
      navigate("/campaigns", { state: { notice: `${campaign.name} deleted.` } });
    } catch (caught) {
      setDeleteFailure(failureMessage(caught));
    } finally {
      setDeletePending(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <CampaignStatusTag status={campaign.status} />
        <Can permission="engage.campaign.campaign.write" scope="ORG">
          <Button variant="ghost" size="sm" className="text-negative" onClick={() => setDeleting(true)}>
            Delete draft
          </Button>
        </Can>
      </div>

      <CampaignSendPanel
        campaign={campaign}
        sender={sender}
        lists={lists}
        dirty={dirty}
        onSent={(sent) => onChanged(`${sent.name} is sending.`)}
        onFocusField={(field) =>
          setFocusRequest((current) => ({ field, nonce: (current?.nonce ?? 0) + 1 }))
        }
      />

      <CampaignForm
        campaign={campaign}
        senders={senders}
        domains={domains}
        lists={lists}
        focusRequest={focusRequest}
        onDirtyChange={setDirty}
        onSaved={onSaved}
      />

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          setDeleteFailure(null);
        }}
        onConfirm={() => void confirmDelete()}
        title="Delete this draft?"
        body={`“${campaign.name}” is removed. Nothing has been sent from it. This can't be undone.`}
        confirmLabel="Delete draft"
        pendingLabel="Deleting…"
        pending={deletePending}
        {...(deleteFailure !== null ? { error: deleteFailure } : {})}
      />
    </div>
  );
}

function CampaignEditor({ campaignId }: { campaignId: string }) {
  const announce = useAnnounce();
  useRouteNotice();
  /* The draft as the service last returned it from a save. Held here rather than re-fetched, so a
     save neither blanks the page to its skeleton nor throws away the form's focus and the test
     send's result. Cleared whenever the page is re-read. */
  const [saved, setSaved] = useState<CampaignOut | null>(null);

  const loader = useCallback(
    () =>
      Promise.all([
        getCampaign(campaignId),
        listSenders(),
        listDomains(),
        listLists({ size: 200 }),
      ]),
    [campaignId],
  );
  const state = useAsync(loader, [campaignId]);
  const { reload } = state;

  const onChanged = useCallback(
    (message: string) => {
      announce(message);
      setSaved(null);
      reload();
    },
    [announce, reload],
  );

  const onSaved = useCallback(
    (next: CampaignOut, message: string) => {
      announce(message);
      setSaved(next);
    },
    [announce],
  );

  const loaded = state.status === "success" ? state.data[0] : null;
  const campaign = saved !== null && loaded !== null && saved.id === loaded.id ? saved : loaded;
  const senders = state.status === "success" ? state.data[1].items : NO_SENDERS;
  const domains = state.status === "success" ? state.data[2].items : NO_DOMAINS;
  const lists = state.status === "success" ? state.data[3].items : NO_LISTS;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow="Campaigns"
        title={campaign?.name ?? "Campaign"}
        {...(campaign !== null && campaign.status !== "draft"
          ? { description: campaign.subject === "" ? "No subject." : campaign.subject }
          : {})}
      />

      {state.status === "loading" ? <LoadingCard /> : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load this campaign" />
      ) : null}

      {campaign !== null && campaign.status === "draft" ? (
        <DraftView
          campaign={campaign}
          senders={senders}
          domains={domains}
          lists={lists}
          onSaved={onSaved}
          onChanged={onChanged}
        />
      ) : null}

      {campaign !== null && campaign.status !== "draft" ? (
        <CampaignReport
          key={campaign.status}
          campaign={campaign}
          sender={senders.find((s) => s.id === campaign.sender_id) ?? null}
          list={lists.find((l) => l.id === campaign.list_id) ?? null}
          onChanged={onChanged}
        />
      ) : null}
    </div>
  );
}

function NewCampaign() {
  const navigate = useNavigate();
  const loader = useCallback(
    () => Promise.all([listSenders(), listDomains(), listLists({ size: 200 })]),
    [],
  );
  const state = useAsync(loader, []);

  const senders = state.status === "success" ? state.data[0].items : NO_SENDERS;
  const domains = state.status === "success" ? state.data[1].items : NO_DOMAINS;
  const lists = state.status === "success" ? state.data[2].items : NO_LISTS;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow="Campaigns"
        title="New campaign"
        description="Write it, check the preview, then send it from the campaign's own page. Saving only ever writes a draft."
      />

      {state.status === "loading" ? <LoadingCard /> : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load what a campaign needs" />
      ) : null}

      {state.status === "success" ? (
        <Can
          permission="engage.campaign.campaign.write"
          scope="ORG"
          fallback={
            <Card className="p-6">
              <p className="text-sm text-muted">
                Drafting a campaign needs the “Draft, edit and delete campaigns” permission. Ask an
                Engage administrator to grant it, or ask them to draft this for you.
              </p>
            </Card>
          }
        >
          <CampaignForm
            campaign={null}
            senders={senders}
            domains={domains}
            lists={lists}
            focusRequest={null}
            onDirtyChange={noop}
            onSaved={(saved) => navigate(`/campaigns/${saved.id}`, { replace: true, state: { notice: `${saved.name} drafted.` } })}
          />
        </Can>
      ) : null}
    </div>
  );
}

function noop(): void {
  // A new campaign has nothing to send yet, so there is no send control to disable.
}

/** The campaign's id comes from the route — never from a prop a caller could get wrong. */
export function CampaignPage() {
  const { campaignId } = useParams<{ campaignId: string }>();
  if (campaignId === undefined) return null;
  return (
    <Announcer>
      <CampaignEditor campaignId={campaignId} />
    </Announcer>
  );
}

export function NewCampaignPage() {
  return (
    <Announcer>
      <NewCampaign />
    </Announcer>
  );
}
