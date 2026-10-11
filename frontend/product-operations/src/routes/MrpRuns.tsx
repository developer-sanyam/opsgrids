import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listMrpRuns } from "@/api/client";
import type { MrpDemandSource, MrpRunOut, MrpRunStatus } from "@/api/types";
import { MrpRunFormDialog } from "@/forms/MrpRunFormDialog";
import { formatDateTime } from "@/lib/format";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  MrpRunStatusPill,
  PageHeader,
  Pagination,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardListIcon, PlusIcon } from "@opsgrids/ui";
import { useToast } from "@opsgrids/ui";

type StatusFilter = MrpRunStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "QUEUED", label: "Queued" },
  { value: "COMPLETED", label: "Completed" },
  { value: "FAILED", label: "Failed" },
];

const SOURCE_LABEL: Record<MrpDemandSource, string> = {
  SALES_ORDER: "Sales orders",
  PRODUCTION_ORDER: "Production orders",
  MANUAL: "Manual",
};

/**
 * Material requirements planning runs (inventory Phase 7). Reading is
 * `operations.manufacturing.mrp.read`; "New run" is `...mrp.run`. A run over a lot of demand is
 * queued for the worker and shows as Queued until it is done — open it to watch.
 */
export function MrpRunsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);

  const state = useAsync(() => listMrpRuns({ status, q: search, page, size }), [status, search, page, size]);

  const columns: Column<MrpRunOut>[] = [
    {
      key: "number",
      header: "Run",
      render: (r) => (
        <Link to={`/manufacturing/mrp/${r.id}`} className="font-semibold text-ink tnum hover:underline">
          {r.number}
        </Link>
      ),
    },
    { key: "status", header: "Status", render: (r) => <MrpRunStatusPill status={r.status} /> },
    {
      key: "sources",
      header: "Planned from",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="text-muted">{r.sources.map((s) => SOURCE_LABEL[s]).join(", ")}</span>,
    },
    {
      key: "demands",
      header: "Demand lines",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum">{r.status === "COMPLETED" ? r.demand_count : "—"}</span>,
    },
    {
      key: "shortages",
      header: "Shortages",
      align: "right",
      render: (r) => <span className="tnum font-semibold">{r.status === "COMPLETED" ? r.shortage_count : "—"}</span>,
    },
    {
      key: "created",
      header: "Started",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => <span className="text-muted tnum">{formatDateTime(r.created_at)}</span>,
    },
  ];

  const filtered = status !== "all" || search !== "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Manufacturing"
        title="Material planning"
        description="Explode demand through the bills of materials, net it against stock and what is on order, and see what to buy or make."
        actions={
          <Can permission="operations.manufacturing.mrp.run" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New run
            </Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search runs"
            placeholder="Search by run number or note…"
            value={search}
            onDebouncedChange={(q) => {
              setSearch(q);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <div className="-mx-1 flex items-center gap-2 overflow-x-auto px-1">
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
          </div>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading runs">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load runs" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(r) => r.id}
              caption="MRP runs"
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title={filtered ? "No runs match" : "No planning runs yet"}
                  description={
                    filtered
                      ? "Try a different status or search, or clear the filters."
                      : "Start a run to see what you need to buy or make."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="runs"
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
        <Can permission="operations.manufacturing.mrp.run" scope="ORG">
          <MrpRunFormDialog
            onClose={() => setCreating(false)}
            onStarted={(run) => {
              setCreating(false);
              if (run.status === "QUEUED") {
                toast.success(`${run.number} is queued. It will be computed in the background.`);
              }
              navigate(`/manufacturing/mrp/${run.id}`);
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
