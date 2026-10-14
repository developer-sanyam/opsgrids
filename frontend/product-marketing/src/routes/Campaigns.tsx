import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { deleteCampaign, getCampaigns } from "@/api/client";
import type { Campaign, CampaignStatus, Channel } from "@/api/types";
import { roas } from "@/data/derive";
import { downloadCsv } from "@/lib/csv";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { MegaphoneIcon, PlusIcon, TABLET_QUERY, cn, useDebouncedValue, useMediaQuery, useToast } from "@opsgrids/ui";
import type { TableSelection } from "@opsgrids/ui";
import { formatMoneyCompact, formatNumber, formatRoas } from "@/lib/format";
import {
  BudgetBar,
  Button,
  Card,
  CampaignStatusPill,
  ChannelBadge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  Pagination,
  PageHeader,
  Pill,
  RowActions,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ChipRow } from "@/ui/ChipRow";
import { CampaignFormDialog } from "@/forms/CampaignFormDialog";

const WRITE_PERMISSION = "marketing.analytics.campaign.write";

type ChannelFilter = Channel | "all";
type StatusFilter = CampaignStatus | "all";

const CHANNEL_FILTERS: { value: ChannelFilter; label: string }[] = [
  { value: "all", label: "All channels" },
  { value: "search", label: "Search" },
  { value: "social", label: "Social" },
  { value: "email", label: "Email" },
  { value: "events", label: "Events" },
];

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "live", label: "Live" },
  { value: "paused", label: "Paused" },
  { value: "draft", label: "Draft" },
  { value: "ended", label: "Ended" },
];

/** ROAS text colour: strong green above 3×, neutral to 2×, negative-red below. */
function roasClass(r: number): string {
  if (r >= 3) return "text-present";
  if (r >= 2) return "text-ink";
  return "text-negative";
}

export function CampaignsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [channel, setChannel] = useState<ChannelFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Campaign | null>(null);
  const [deleting, setDeleting] = useState<Campaign | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  // On a phone a tick box on every card is clutter; selecting is a mode the person turns on.
  const wide = useMediaQuery(TABLET_QUERY, true);
  const [selecting, setSelecting] = useState(false);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");

  // The sidebar's ⌘K search lands here with `?q=` — sync it into local state without touching the
  // URL further (typing afterwards just diverges from it, harmlessly).
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  // "New campaign" on the Overview lands here with `?new=1` rather than pretending to create one
  // itself. Consumed once and cleared from the URL so a reload or a Back doesn't replay it.
  useEffect(() => {
    if (searchParams.get("new") !== "1") return;
    setCreating(true);
    const next = new URLSearchParams(searchParams);
    next.delete("new");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const campaigns = useAsync(
    () => getCampaigns({ q: debouncedSearch, channel, status, page, size }),
    [debouncedSearch, channel, status, page, size],
  );

  const remove = useMutation((c: Campaign) => deleteCampaign(c.id), {
    onSuccess: (_result, c) => {
      setDeleting(null);
      toast.success(`${c.name} deleted.`);
      campaigns.reload();
    },
  });

  const hasFilters = debouncedSearch !== "" || channel !== "all" || status !== "all";

  const columns: Column<Campaign>[] = [
    {
      key: "name",
      header: "Campaign",
      mobile: "title",
      render: (c) => (
        <button
          type="button"
          onClick={() => navigate(`/campaigns/${c.id}`)}
          className="flex flex-col gap-1.5 text-left"
        >
          <span className="font-semibold text-ink hover:text-accent transition-colors w-fit">
            {c.name}
          </span>
          <span className="flex items-center gap-2">
            <ChannelBadge channel={c.channel} />
            <span className="hidden text-xs text-muted md:inline">{c.goal}</span>
          </span>
        </button>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (c) => <CampaignStatusPill status={c.status} />,
    },
    {
      key: "budget",
      header: "Budget",
      render: (c) =>
        c.status === "draft" ? (
          <span className="text-xs text-faint">Not launched</span>
        ) : (
          <BudgetBar spentMinor={c.spent_minor} budgetMinor={c.budget_minor} currency={c.currency} />
        ),
    },
    {
      key: "leads",
      header: "Leads",
      align: "right",
      render: (c) => <span className="tnum text-ink-soft">{formatNumber(c.leads)}</span>,
    },
    {
      key: "revenue",
      header: "Revenue",
      align: "right",
      render: (c) => (
        <span className="tnum font-semibold text-ink">
          {formatMoneyCompact(c.revenue_minor, c.currency)}
        </span>
      ),
    },
    {
      key: "roas",
      header: "ROAS",
      align: "right",
      render: (c) => {
        const r = roas(c.revenue_minor, c.spent_minor);
        return <span className={cn("tnum font-bold", roasClass(r))}>{formatRoas(r)}</span>;
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (c) => (
        <RowActions
          label={c.name}
          inlineLimit={1}
          actions={[
            { label: "View", onSelect: () => navigate(`/campaigns/${c.id}`) },
            { label: "Edit", onSelect: () => setEditing(c), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(c), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  const campaignSelection: TableSelection<Campaign> | undefined =
    campaigns.status === "success" && (wide || selecting)
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) =>
            setSelected(next ? new Set(campaigns.data.items.map((c) => c.id)) : new Set()),
          label: (c) => c.name,
        }
      : undefined;

  function exportSelectedCampaigns() {
    if (campaigns.status !== "success") return;
    const chosen = campaigns.data.items.filter((c) => selected.has(c.id));
    downloadCsv(
      "campaigns.csv",
      ["Name", "Channel", "Status", "Leads", "Revenue", "ROAS"],
      chosen.map((c) => [
        c.name,
        c.channel,
        c.status,
        String(c.leads),
        formatMoneyCompact(c.revenue_minor, c.currency),
        formatRoas(roas(c.revenue_minor, c.spent_minor)),
      ]),
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Campaigns"
        title="Campaigns"
        actions={
          <Can permission={WRITE_PERMISSION} scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New campaign
            </Button>
          </Can>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search campaigns"
            placeholder="Search by name, goal or owner…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-xs"
          />
          <ChipRow label="Filter by status">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </ChipRow>
        </div>
        <div className="border-t border-line pt-4">
        <ChipRow label="Filter by channel">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Channel
          </span>
          {CHANNEL_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={channel === f.value}
              onClick={() => {
                setChannel(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </ChipRow>
        </div>
        {!wide && campaigns.status === "success" && campaigns.data.items.length > 0 ? (
          <Button
            variant="outline"
            aria-pressed={selecting}
            onClick={() => {
              setSelecting((on) => !on);
              setSelected(new Set());
            }}
          >
            {selecting ? "Done selecting" : "Select campaigns"}
          </Button>
        ) : null}
      </Card>

      {/* Table */}
      <Card className="p-2 sm:p-3">
        {campaigns.status === "loading" ? (
          <div className="flex flex-col gap-2 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {campaigns.status === "error" ? (
          <div className="p-4">
            <ErrorState
              title="Couldn't load campaigns"
              message={problemMessage(campaigns.error)}
              onRetry={campaigns.reload}
            />
          </div>
        ) : null}

        {campaigns.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={campaigns.data.items}
              rowKey={(c) => c.id}
              caption="Campaigns with channel, status, budget, leads, revenue and ROAS"
              onRowClick={(c) => navigate(`/campaigns/${c.id}`)}
              mobileCard="auto"
              selection={campaignSelection}
              bulkActions={
                <Button variant="outline" size="sm" onClick={exportSelectedCampaigns}>
                  Export selected
                </Button>
              }
              emptyState={
                <EmptyState
                  icon={<MegaphoneIcon className="size-6" />}
                  title={hasFilters ? "No campaigns match these filters" : "No campaigns yet"}
                  description={
                    hasFilters
                      ? "Nothing matches these filters. Clear the search or pick a different channel or status."
                      : "Create the first campaign to start tracking spend against revenue."
                  }
                  action={
                    hasFilters ? null : (
                      <Can permission={WRITE_PERMISSION} scope="ORG">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          New campaign
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={campaigns.data.page}
              pages={campaigns.data.pages}
              total={campaigns.data.total}
              size={campaigns.data.size}
              noun="campaigns"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating ? (
        <CampaignFormDialog
          campaign={null}
          onClose={() => setCreating(false)}
          onSaved={(saved) => {
            setCreating(false);
            toast.success(`${saved.name} created.`);
            campaigns.reload();
          }}
        />
      ) : null}

      {editing ? (
        <CampaignFormDialog
          campaign={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast.success(`${saved.name} saved.`);
            campaigns.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this campaign?"
        body={`${deleting?.name} is removed from Campaigns, Channels and the Overview. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
