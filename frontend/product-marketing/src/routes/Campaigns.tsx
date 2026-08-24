import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { getCampaigns } from "@/api/client";
import type { Campaign, CampaignStatus, Channel } from "@/api/types";
import { roas } from "@/data/derive";
import { useAsync } from "@/lib/useAsync";
import { cn } from "@/lib/cn";
import {
  campaignStatusLabel,
  channelLabel,
  formatMoneyCompact,
  formatNumber,
  formatRoas,
} from "@/lib/format";
import {
  BudgetBar,
  Button,
  Card,
  CampaignStatusPill,
  ChannelBadge,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import { ArrowRightIcon, ChevronDown, MegaphoneIcon, PlusIcon } from "@/ui/icons";

const NO_CAMPAIGNS: Campaign[] = [];

type ChannelFilter = Channel | "all";
type StatusFilter = CampaignStatus | "all";
type SortKey = "name" | "spent" | "leads" | "revenue" | "roas";
type SortDir = "asc" | "desc";

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

function value(c: Campaign, key: SortKey): number | string {
  switch (key) {
    case "name":
      return c.name.toLowerCase();
    case "spent":
      return c.spent_minor;
    case "leads":
      return c.leads;
    case "revenue":
      return c.revenue_minor;
    case "roas":
      return roas(c.revenue_minor, c.spent_minor);
  }
}

export function CampaignsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [channel, setChannel] = useState<ChannelFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("revenue");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const campaigns = useAsync(getCampaigns, []);
  const items = useMemo(
    () => (campaigns.status === "success" ? campaigns.data.items : NO_CAMPAIGNS),
    [campaigns],
  );

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = items.filter((c) => {
      const matchesQuery =
        q === "" ||
        c.name.toLowerCase().includes(q) ||
        c.goal.toLowerCase().includes(q) ||
        c.owner.toLowerCase().includes(q);
      const matchesChannel = channel === "all" || c.channel === channel;
      const matchesStatus = status === "all" || c.status === status;
      return matchesQuery && matchesChannel && matchesStatus;
    });
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = value(a, sortKey);
      const vb = value(b, sortKey);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
  }, [items, search, channel, status, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "name" ? "asc" : "desc");
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Campaigns"
        title="Campaigns"
        actions={
          <>
            <span className="hidden rounded-full bg-surface shadow-card px-4 h-11 sm:inline-flex items-center gap-2 text-sm font-semibold">
              <MegaphoneIcon className="size-4 text-muted" />
              <span className="tnum">{items.length}</span>
              <span className="text-muted font-medium">total</span>
            </span>
            <Button leadingIcon={<PlusIcon className="size-4" />}>New campaign</Button>
          </>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search campaigns"
            placeholder="Search by name, goal or owner…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Channel
          </span>
          {CHANNEL_FILTERS.map((f) => (
            <Pill key={f.value} active={channel === f.value} onClick={() => setChannel(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
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
            <ErrorState message={campaigns.error.message} />
          </div>
        ) : null}

        {campaigns.status === "success" ? (
          rows.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={<MegaphoneIcon className="size-6" />}
                title="No matching campaigns"
                description="Nothing matches these filters. Clear the search or pick a different channel or status."
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse text-sm">
                <caption className="sr-only">
                  Campaigns with channel, status, budget, leads, revenue and ROAS
                </caption>
                <thead>
                  <tr className="border-b border-line">
                    <SortableTh
                      label="Campaign"
                      col="name"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                      align="left"
                    />
                    <th
                      scope="col"
                      className="py-3 px-3 text-left text-xs font-semibold uppercase tracking-wide text-faint"
                    >
                      Status
                    </th>
                    <th
                      scope="col"
                      className="py-3 px-3 text-left text-xs font-semibold uppercase tracking-wide text-faint w-48"
                    >
                      Budget
                    </th>
                    <SortableTh
                      label="Leads"
                      col="leads"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                      align="right"
                    />
                    <SortableTh
                      label="Revenue"
                      col="revenue"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                      align="right"
                    />
                    <SortableTh
                      label="ROAS"
                      col="roas"
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={toggleSort}
                      align="right"
                    />
                    <th scope="col" className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => {
                    const r = roas(c.revenue_minor, c.spent_minor);
                    return (
                      <tr
                        key={c.id}
                        onClick={() => navigate(`/campaigns/${c.id}`)}
                        className="group cursor-pointer border-b border-line/70 last:border-0 transition-colors hover:bg-ground/60"
                      >
                        <td className="py-3 px-3 align-middle">
                          <div className="flex flex-col gap-1.5">
                            <Link
                              to={`/campaigns/${c.id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="font-semibold text-ink hover:text-accent transition-colors w-fit"
                            >
                              {c.name}
                            </Link>
                            <span className="flex items-center gap-2">
                              <ChannelBadge channel={c.channel} />
                              <span className="hidden text-xs text-muted md:inline">{c.goal}</span>
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-3 align-middle">
                          <CampaignStatusPill status={c.status} />
                        </td>
                        <td className="py-3 px-3 align-middle">
                          {c.status === "draft" ? (
                            <span className="text-xs text-faint">Not launched</span>
                          ) : (
                            <BudgetBar
                              spentMinor={c.spent_minor}
                              budgetMinor={c.budget_minor}
                              currency={c.currency}
                            />
                          )}
                        </td>
                        <td className="py-3 px-3 align-middle text-right tnum text-ink-soft">
                          {formatNumber(c.leads)}
                        </td>
                        <td className="py-3 px-3 align-middle text-right tnum font-semibold text-ink">
                          {formatMoneyCompact(c.revenue_minor, c.currency)}
                        </td>
                        <td
                          className={cn(
                            "py-3 px-3 align-middle text-right tnum font-bold",
                            roasClass(r),
                          )}
                        >
                          {formatRoas(r)}
                        </td>
                        <td className="py-3 pr-3 align-middle text-right">
                          <ArrowRightIcon className="ml-auto size-4 text-faint transition-colors group-hover:text-accent" />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>

      {campaigns.status === "success" && rows.length > 0 ? (
        <p className="text-center text-sm text-faint">
          Showing {rows.length} of {items.length} campaigns
          {channel !== "all" ? ` · ${channelLabel(channel)}` : ""}
          {status !== "all" ? ` · ${campaignStatusLabel(status)}` : ""}
        </p>
      ) : null}
    </div>
  );
}

function SortableTh({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  align,
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  align: "left" | "right";
}) {
  const active = sortKey === col;
  return (
    <th
      scope="col"
      aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(
        "py-3 px-3 text-xs font-semibold uppercase tracking-wide text-faint",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      <button
        type="button"
        onClick={() => onSort(col)}
        className={cn(
          "inline-flex items-center gap-1 rounded-md transition-colors hover:text-ink",
          align === "right" ? "flex-row-reverse" : "",
          active ? "text-ink" : "",
        )}
      >
        {label}
        <ChevronDown
          className={cn(
            "size-3.5 transition-transform",
            active ? "text-accent" : "text-faint/60",
            active && sortDir === "asc" ? "rotate-180" : "",
          )}
        />
      </button>
    </th>
  );
}
