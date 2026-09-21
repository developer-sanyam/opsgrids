import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { deleteCampaign, getAudiences, getCampaign, getCampaignWeekly, updateCampaign } from "@/api/client";
import type { Audience, CampaignStatus } from "@/api/types";
import { cac, costPerLead, roas } from "@/data/derive";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  channelLabel,
  formatDate,
  formatMoney,
  formatMoneyCompact,
  formatNumber,
  formatRoas,
} from "@/lib/format";
import {
  BudgetBar,
  Button,
  Card,
  CampaignPerformanceChart,
  CampaignStatusPill,
  ChannelBadge,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  SectionHeader,
  Skeleton,
  StatTile,
} from "@/ui";
import { Announcer, ArrowRightIcon, TargetIcon, UsersIcon, useAnnounce, useToast } from "@opsgrids/ui";
import { CampaignFormDialog } from "@/forms/CampaignFormDialog";

const WRITE_PERMISSION = "marketing.analytics.campaign.write";

/** What each status may legally move to next — mirrors `app.domain.CAMPAIGN_TRANSITIONS`, kept
 *  in the UI only to decide which buttons to offer; the server is the one that actually enforces
 *  it, with a 409 for anything this graph would not have offered in the first place. */
const NEXT_ACTIONS: Record<CampaignStatus, { status: CampaignStatus; label: string; confirm: boolean }[]> = {
  draft: [{ status: "live", label: "Launch", confirm: false }],
  live: [
    { status: "paused", label: "Pause", confirm: false },
    { status: "ended", label: "End", confirm: true },
  ],
  paused: [
    { status: "live", label: "Resume", confirm: false },
    { status: "ended", label: "End", confirm: true },
  ],
  ended: [],
};

function CampaignDetailScreen() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const announce = useAnnounce();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [ending, setEnding] = useState(false);

  const loader = useCallback(
    () => Promise.all([getCampaign(id), getAudiences({ size: 200 }), getCampaignWeekly(id)]),
    [id],
  );
  const detail = useAsync(loader, [id]);

  const changeStatus = useMutation((status: CampaignStatus) => updateCampaign(id, { status }), {
    onSuccess: (updated) => {
      setEnding(false);
      announce(`Campaign ${updated.status === "ended" ? "ended" : `moved to ${updated.status}`}.`);
      detail.reload();
    },
  });

  const remove = useMutation(() => deleteCampaign(id), {
    onSuccess: () => {
      const name = detail.status === "success" ? detail.data[0].name : "Campaign";
      toast.success(`${name} deleted.`);
      navigate("/campaigns");
    },
  });

  if (detail.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Skeleton className="h-24 w-full rounded-card" />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-tile" />
          ))}
        </div>
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }

  if (detail.status === "error") {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink />
        <Card className="p-8">
          <ErrorState
            title="Couldn't load this campaign"
            message={`${problemMessage(detail.error)} It may have been renamed or removed.`}
            onRetry={detail.reload}
          />
        </Card>
      </div>
    );
  }

  const [campaign, audiencePage, weekly] = detail.data;
  const audience: Audience | null = audiencePage.items.find((a) => a.id === campaign.audience_id) ?? null;

  const r = roas(campaign.revenue_minor, campaign.spent_minor);
  const cpl = costPerLead(campaign.spent_minor, campaign.leads);
  const acquisition = cac(campaign.spent_minor, campaign.conversions);
  const isDraft = campaign.status === "draft";
  const actions = NEXT_ACTIONS[campaign.status];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      {/* Header */}
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <ChannelBadge channel={campaign.channel} />
            <CampaignStatusPill status={campaign.status} />
          </div>
          <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
            {campaign.name}
          </h1>
          <p className="text-sm text-muted">
            {channelLabel(campaign.channel)} · owned by{" "}
            <span className="font-semibold text-ink-soft">{campaign.owner || "Unassigned"}</span> ·{" "}
            {formatDate(campaign.start_date)} – {formatDate(campaign.end_date)}
          </p>
        </div>
        <Can permission={WRITE_PERMISSION} scope="ORG">
          <div className="flex flex-wrap items-center gap-2">
            {actions.map((action) =>
              action.confirm ? (
                <Button
                  key={action.status}
                  variant="outline"
                  onClick={() => setEnding(true)}
                >
                  {action.label}
                </Button>
              ) : (
                <Button
                  key={action.status}
                  variant="outline"
                  loading={changeStatus.pending}
                  onClick={() => void changeStatus.mutate(action.status)}
                >
                  {action.label}
                </Button>
              ),
            )}
            <Button variant="outline" onClick={() => setEditing(true)}>
              Edit campaign
            </Button>
            <Button variant="danger" onClick={() => setDeleting(true)}>
              Delete
            </Button>
          </div>
        </Can>
      </header>

      {changeStatus.error && !ending ? (
        <InlineAlert tone="danger" title={problemMessage(changeStatus.error)} />
      ) : null}

      {/* Metric tiles */}
      <section
        aria-label="Campaign metrics"
        className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6"
      >
        <StatTile
          label="Revenue"
          value={formatMoneyCompact(campaign.revenue_minor, campaign.currency)}
          sub="attributed"
        />
        <StatTile
          label="Pipeline"
          value={formatMoneyCompact(campaign.pipeline_minor, campaign.currency)}
          sub="open value"
        />
        <StatTile label="Leads" value={formatNumber(campaign.leads)} sub="qualified" />
        <StatTile label="Deals" value={formatNumber(campaign.conversions)} sub="closed won" />
        <StatTile label="ROAS" value={formatRoas(r)} sub="revenue ÷ spend" />
        <StatTile
          label="Cost / lead"
          value={cpl === null ? "—" : formatMoney(cpl, campaign.currency)}
          sub="spend ÷ leads"
        />
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3 items-start">
        {/* Performance */}
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader
            title="Weekly performance"
            subtitle="Revenue and spend across the campaign flight"
          />
          {isDraft ? (
            <div className="grid place-items-center rounded-tile bg-ground/60 py-16 text-center">
              <div className="flex max-w-xs flex-col gap-1">
                <p className="text-base font-bold text-ink">Not launched yet</p>
                <p className="text-sm text-muted">
                  This campaign is still a draft. Performance will appear here once it goes live.
                </p>
              </div>
            </div>
          ) : (
            <CampaignPerformanceChart weeks={weekly} currency={campaign.currency} />
          )}
        </Card>

        {/* Right column: budget + audience */}
        <div className="flex flex-col gap-6">
          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Budget & goal" />
            {campaign.budget_minor === 0 ? (
              <div className="flex flex-col gap-1.5">
                <span className="display text-2xl font-extrabold tnum">
                  {formatMoney(campaign.spent_minor, campaign.currency)}
                </span>
                <span className="text-xs text-faint">No budget set</span>
              </div>
            ) : (
              <BudgetBar
                spentMinor={campaign.spent_minor}
                budgetMinor={campaign.budget_minor}
                currency={campaign.currency}
              />
            )}
            <div className="flex items-start gap-2.5 rounded-tile bg-ground/60 px-3 py-3">
              <TargetIcon className="mt-0.5 size-5 shrink-0 text-accent" />
              <div className="flex flex-col leading-tight">
                <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                  Primary goal
                </span>
                <span className="text-sm font-semibold text-ink">{campaign.goal || "No goal set"}</span>
              </div>
            </div>
            {acquisition !== null ? (
              <p className="text-sm text-muted">
                Customer acquisition cost{" "}
                <span className="font-semibold text-ink tnum">
                  {formatMoney(acquisition, campaign.currency)}
                </span>{" "}
                across {formatNumber(campaign.conversions)} deals.
              </p>
            ) : null}
          </Card>

          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Target audience" />
            {audience ? (
              <>
                <div className="flex items-center gap-3">
                  <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                    <UsersIcon className="size-5" />
                  </span>
                  <div className="flex flex-col leading-tight">
                    <span className="font-bold text-ink">{audience.name}</span>
                    <span className="text-sm text-muted tnum">
                      {formatNumber(audience.size)} reachable
                    </span>
                  </div>
                </div>
                <p className="rounded-tile bg-ground/60 px-3 py-2.5 text-sm text-ink-soft">
                  {audience.criteria || "No criteria on file."}
                </p>
                <Link
                  to="/audiences"
                  className="inline-flex h-10 items-center justify-center gap-1.5 rounded-full border border-line bg-surface text-sm font-semibold text-ink hover:bg-ground transition-colors"
                >
                  View in Audiences
                  <ArrowRightIcon className="size-4" />
                </Link>
              </>
            ) : (
              <p className="text-sm text-muted">No audience is attached to this campaign.</p>
            )}
          </Card>
        </div>
      </div>

      {editing ? (
        <CampaignFormDialog
          campaign={campaign}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            setEditing(false);
            toast.success(`${saved.name} saved.`);
            detail.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          remove.reset();
        }}
        onConfirm={() => void remove.mutate(undefined)}
        title="Delete this campaign?"
        body={`${campaign.name} is removed from Campaigns, Channels and the Overview. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />

      <ConfirmDialog
        open={ending}
        onClose={() => {
          setEnding(false);
          changeStatus.reset();
        }}
        onConfirm={() => void changeStatus.mutate("ended")}
        title="End this campaign?"
        body={`${campaign.name} moves to Ended, which is final — it can't be relaunched or moved back to live or paused. Start a new campaign instead.`}
        confirmLabel="End campaign"
        pendingLabel="Ending…"
        pending={changeStatus.pending}
        {...(changeStatus.error ? { error: problemMessage(changeStatus.error) } : {})}
      />
    </div>
  );
}

export function CampaignDetailPage() {
  return (
    <Announcer>
      <CampaignDetailScreen />
    </Announcer>
  );
}

function BackLink() {
  return (
    <Link
      to="/campaigns"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      All campaigns
    </Link>
  );
}
