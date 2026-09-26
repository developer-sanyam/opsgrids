import { useCallback, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { ArrowLeftIcon } from "@opsgrids/ui";

import { deleteCampaign, getCampaign } from "@/api/client";
import type { CampaignOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import {
  audienceName,
  isDenied,
  loadDomains,
  loadLists,
  loadRefs,
  loadSenders,
  notRead,
  resolveLists,
  senderName,
} from "@/lib/refs";
import type { Refs, ResolvedLists } from "@/lib/refs";
import { useFocusHeading } from "@/lib/useFocusHeading";
import { useRouteNotice } from "@/lib/useRouteNotice";
import {
  Announcer,
  Button,
  CampaignStatusTag,
  Card,
  ConfirmDialog,
  LoadFailure,
  PageHeader,
  PageHeading,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { CampaignForm } from "./CampaignForm";
import type { FocusRequest } from "./CampaignForm";
import { CampaignReport } from "./CampaignReport";
import type { StatusChanged } from "./CampaignReport";
import { CampaignSendPanel } from "./CampaignSendPanel";

/**
 * The two campaign routes: `/campaigns/new` drafts one, `/campaigns/:campaignId` opens it.
 *
 * A campaign is one of two things to look at. While it is a `draft` it is a form — edited, tested,
 * sent or deleted. Once it has left `draft` it is a record — `CampaignReport` — and the service
 * refuses every edit (`services.update_campaign`), so nothing here offers one. Which of the two is
 * shown is the service's `status`, never a guess.
 *
 * The campaign is the one read this screen cannot do without. Its reference data — senders, their
 * domains, lists — is read beside it and **settles on its own** (`lib/refs.ts`): a role that may
 * read campaigns but not senders still gets the campaign, with neutral labels where a sender's name
 * would be, and a list the paged read did not reach is read by id so a campaign always names the
 * list it is actually addressed to.
 */

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
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <ArrowLeftIcon className="size-4" />
      All campaigns
    </Link>
  );
}

/** A draft: the form, the send gate beside it, and delete. */
function DraftView({
  campaign,
  refs,
  resolved,
  onSaved,
  onChanged,
}: {
  campaign: CampaignOut;
  refs: Refs;
  resolved: ResolvedLists;
  /** The service accepted an edit: the campaign as it now holds it. No reload — the form keeps its
   *  focus and the page keeps its place. */
  onSaved: (campaign: CampaignOut, message: string) => void;
  /** The campaign's *status* moved (sent), so the page must be re-read and re-shown. */
  onChanged: StatusChanged;
}) {
  const navigate = useNavigate();
  const [dirty, setDirty] = useState(false);
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);

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
        senderLabel={senderName(campaign, refs.senders)}
        resolved={resolved}
        dirty={dirty}
        onSent={(sent) => onChanged(`${sent.name} is sending.`)}
        onScheduled={(_scheduled, message) => onChanged(message)}
        onFocusField={(field) =>
          setFocusRequest((current) => ({ field, nonce: (current?.nonce ?? 0) + 1 }))
        }
      />

      <CampaignForm
        campaign={campaign}
        refs={refs}
        resolved={resolved}
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
  const titleRef = useRef<HTMLHeadingElement>(null);
  useRouteNotice(titleRef);
  /* The draft as the service last returned it from a save. Held here rather than re-fetched, so a
     save neither blanks the page to its skeleton nor throws away the form's focus and the test
     send's result. Cleared whenever the page is re-read. */
  const [saved, setSaved] = useState<CampaignOut | null>(null);

  /* Reads what the campaign's *status* needs, and no more. Both views need the campaign and the
     senders (to name who it goes as), so those two go together. Only a draft is a form, and only a
     form needs the domains (to hide senders whose domain lapsed) and the lists (to pick one): a sent
     campaign's report names its own list by id and reads nothing else. A typical draft is four
     calls, a typical report three — see the budget in `lib/refs.ts`. */
  const loader = useCallback(async () => {
    const [campaign, senders] = await Promise.all([getCampaign(campaignId), loadSenders()]);
    const listIds = campaign.list_id === null ? [] : [campaign.list_id];
    if (campaign.status !== "draft") {
      const resolved = await resolveLists(listIds, []);
      const refs: Refs = { senders, domains: notRead(), lists: notRead() };
      return { campaign, refs, resolved };
    }
    const [domains, lists] = await Promise.all([loadDomains(), loadLists()]);
    // A by-id read needs the same permission the paged read of lists just failed for.
    const resolved = await resolveLists(listIds, lists.items, { skip: isDenied(lists.error) });
    return { campaign, refs: { senders, domains, lists }, resolved };
  }, [campaignId]);
  const state = useAsync(loader, [campaignId]);
  const { reload } = state;
  const focusHeading = useFocusHeading(titleRef);
  /* Everything below the heading that a re-read replaces. Where focus is, relative to this, is what
     decides whether a change the *service* made may move it. */
  const contentRef = useRef<HTMLDivElement>(null);

  /* A status change unmounts whichever control caused it — Send, Cancel, Resume — and the browser
     would drop focus to the document, so for a change the person asked for, focus goes to the
     heading first and the page then re-reads.

     A change the *service* made — a poll that found the campaign sent — is decided differently, and
     **at the moment it is requested**: the person did nothing, and may be anywhere on the page. Focus
     is taken only if it would be lost anyway — nothing is focused, or it is on something inside the
     content that is about to be replaced, or inside a dialog that is about to be replaced with it.
     Someone on the "All campaigns" link, or in the sidebar, keeps exactly where they are. */
  const onChanged = useCallback<StatusChanged>(
    (message, options) => {
      announce(message);
      const active = document.activeElement;
      const wouldBeLost =
        active === null ||
        active === document.body ||
        contentRef.current?.contains(active) === true ||
        active.closest('[role="dialog"]') !== null;
      if (options?.automatic !== true || wouldBeLost) focusHeading();
      setSaved(null);
      reload();
    },
    [announce, reload, focusHeading],
  );

  const onSaved = useCallback(
    (next: CampaignOut, message: string) => {
      announce(message);
      setSaved(next);
    },
    [announce],
  );

  const data = state.status === "success" ? state.data : null;
  const loaded = data === null ? null : data.campaign;
  const campaign = saved !== null && loaded !== null && saved.id === loaded.id ? saved : loaded;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeading
        ref={titleRef}
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

      {/* `contents`: this wrapper exists to be measured against, and takes no part in the layout. */}
      <div ref={contentRef} className="contents">
        {data !== null && campaign !== null && campaign.status === "draft" ? (
          <DraftView
            campaign={campaign}
            refs={data.refs}
            resolved={data.resolved}
            onSaved={onSaved}
            onChanged={onChanged}
          />
        ) : null}

        {data !== null && campaign !== null && campaign.status !== "draft" ? (
          <CampaignReport
            key={campaign.status}
            campaign={campaign}
            senderLabel={senderName(campaign, data.refs.senders)}
            audienceLabel={audienceName(campaign, data.resolved)}
            onChanged={onChanged}
          />
        ) : null}
      </div>
    </div>
  );
}

function NewCampaign() {
  const navigate = useNavigate();
  const loader = useCallback(() => loadRefs(), []);
  const state = useAsync(loader, []);

  const refs = state.status === "success" ? state.data : null;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader
        eyebrow="Campaigns"
        title="New campaign"
        description="Write it, check the preview, then send it from the campaign's own page. Saving only ever writes a draft."
      />

      {state.status === "loading" ? <LoadingCard /> : null}

      {/* A campaign is written as a sender, so a sender read that was refused leaves nothing to
          choose: that is the one reference read whose failure is the screen's failure. Lists and
          domains are only ever help, and a failure there is said inside the form. */}
      {refs !== null && refs.senders.error !== null ? (
        <LoadFailure error={refs.senders.error} title="Couldn't load what a campaign needs" />
      ) : null}

      {refs !== null && refs.senders.error === null ? (
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
            refs={refs}
            resolved={{ lists: refs.lists.items, gone: new Set() }}
            focusRequest={null}
            onDirtyChange={noop}
            onSaved={(saved) =>
              navigate(`/campaigns/${saved.id}`, {
                replace: true,
                state: { notice: `${saved.name} drafted.` },
              })
            }
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
