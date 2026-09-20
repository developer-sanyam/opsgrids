import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { listDeals } from "@/api/client";
import type { Company, Deal, DealStage } from "@/api/types";
import { dealsByStage } from "@/data/derive";
import { useDirectory } from "@/data/directoryContext";
import { companyName } from "@/data/lookups";
import { STAGES } from "@/data/stages";
import { HandshakeIcon, KanbanIcon, LayoutIcon, PlusIcon, cn } from "@opsgrids/ui";
import { formatDate, formatMoney } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  KanbanBoard,
  OwnerBadge,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  StagePill,
} from "@/ui";
import type { Column } from "@/ui";

type StageFilter = DealStage | "all";
type View = "board" | "list";

const STAGE_FILTERS: { value: StageFilter; label: string }[] = [
  { value: "all", label: "All stages" },
  ...STAGES.map((s) => ({ value: s.stage as StageFilter, label: s.label })),
];

const NO_DEALS: Deal[] = [];

function buildListColumns(companies: Company[]): Column<Deal>[] {
  return [
  {
    key: "name",
    header: "Deal",
    render: (d) => (
      <div className="flex flex-col leading-tight">
        <Link
          to={`/deals/${d.id}`}
          className="font-semibold text-ink hover:text-accent transition-colors"
        >
          {d.name}
        </Link>
        <span className="text-xs text-muted">{companyName(companies, d.company_id)}</span>
      </div>
    ),
  },
  { key: "stage", header: "Stage", render: (d) => <StagePill stage={d.stage} /> },
  {
    key: "value",
    header: "Value",
    align: "right",
    render: (d) => <span className="tnum font-semibold text-ink">{formatMoney(d.value_minor, d.currency)}</span>,
  },
  {
    key: "owner",
    header: "Owner",
    cellClassName: "hidden md:table-cell",
    headerClassName: "hidden md:table-cell",
    render: (d) => <OwnerBadge ownerId={d.owner_id} />,
  },
  {
    key: "close",
    header: "Close date",
    cellClassName: "hidden sm:table-cell",
    headerClassName: "hidden sm:table-cell",
    render: (d) => <span className="tnum text-ink-soft">{formatDate(d.close_date)}</span>,
  },
  {
    key: "prob",
    header: "Prob.",
    align: "right",
    cellClassName: "hidden lg:table-cell",
    headerClassName: "hidden lg:table-cell",
    render: (d) => <span className="tnum text-muted">{d.probability}%</span>,
  },
  ];
}

export function DealsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [stage, setStage] = useState<StageFilter>("all");
  const [ownerId, setOwnerId] = useState<string | "all">("all");
  const [view, setView] = useState<View>("board");
  const { owners: ownerOptions, companies } = useDirectory();

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const loader = useCallback(() => listDeals({ search, stage, ownerId }), [search, stage, ownerId]);
  const deals = useAsync(loader, [search, stage, ownerId]);

  const items = useMemo(
    () => (deals.status === "success" ? deals.data.items : NO_DEALS),
    [deals],
  );
  const groups = useMemo(() => dealsByStage(items), [items]);
  const listColumns = useMemo(() => buildListColumns(companies), [companies]);
  const total = items.length;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Deals"
        actions={
          <>
            <div
              role="tablist"
              aria-label="View"
              className="hidden items-center rounded-full bg-surface p-0.5 shadow-card sm:flex"
            >
              <button
                type="button"
                role="tab"
                aria-selected={view === "board"}
                onClick={() => setView("board")}
                className={cn(
                  "inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-sm font-semibold transition-colors",
                  view === "board" ? "bg-accent text-white" : "text-muted hover:text-ink",
                )}
              >
                <KanbanIcon className="size-4" />
                Board
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === "list"}
                onClick={() => setView("list")}
                className={cn(
                  "inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-sm font-semibold transition-colors",
                  view === "list" ? "bg-accent text-white" : "text-muted hover:text-ink",
                )}
              >
                <LayoutIcon className="size-4" />
                List
              </button>
            </div>
            <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/deals")}>
              New deal
            </Button>
          </>
        }
      />

      {/* Controls */}
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search deals"
            placeholder="Search by deal name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STAGE_FILTERS.map((f) => (
              <Pill key={f.value} active={stage === f.value} onClick={() => setStage(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center rounded-full bg-ground px-4">
            <span className="sr-only">Filter by owner</span>
            <select
              value={ownerId}
              onChange={(e) => setOwnerId(e.target.value)}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              <option value="all">All owners</option>
              {ownerOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.full_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {deals.status === "loading" ? (
        <div className="flex gap-4 overflow-hidden">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex w-[264px] shrink-0 flex-col gap-2.5 rounded-card bg-ground/60 p-2.5">
              <Skeleton className="h-6 w-32" />
              <Skeleton className="h-24 w-full rounded-tile" />
              <Skeleton className="h-24 w-full rounded-tile" />
            </div>
          ))}
        </div>
      ) : null}

      {deals.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={deals.error.message} />
        </Card>
      ) : null}

      {deals.status === "success" ? (
        total === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<HandshakeIcon className="size-6" />}
              title="No matching deals"
              description="No deals match these filters. Try another stage, owner, or clear the search."
            />
          </Card>
        ) : view === "board" ? (
          <KanbanBoard groups={groups} />
        ) : (
          <Card className="p-2 sm:p-4">
            <DataTable
              caption="All deals in the pipeline"
              columns={listColumns}
              rows={items}
              rowKey={(d) => d.id}
            />
          </Card>
        )
      ) : null}
    </div>
  );
}
