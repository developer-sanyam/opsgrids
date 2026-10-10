import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { listRequisitions } from "@/api/client";
import type { RequisitionStatus, RequisitionSummaryOut } from "@/api/types";
import { Can, useAsync } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  LoadError,
  PageHeader,
  Pagination,
  Pill,
  RequisitionStatusPill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardListIcon, PlusIcon, useDebouncedValue } from "@opsgrids/ui";

type StatusFilter = RequisitionStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "SUBMITTED", label: "Submitted" },
  { value: "APPROVED", label: "Approved" },
  { value: "REJECTED", label: "Rejected" },
  { value: "CONVERTED", label: "Converted" },
  { value: "CANCELLED", label: "Cancelled" },
];

const WRITE = "operations.procurement.requisition.write";

/**
 * Purchase requisitions — the internal request that comes before a purchase order. Anyone who can
 * raise one sees their own; the server decides which rows a viewer's scope reaches.
 */
export function PurchaseRequisitionsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const state = useAsync(
    () => listRequisitions({ q: debouncedSearch, status, page, size }),
    [debouncedSearch, status, page, size],
  );

  const columns: Column<RequisitionSummaryOut>[] = [
    {
      key: "number",
      header: "Number",
      render: (r) => <span className="font-semibold text-ink tnum">{r.number}</span>,
    },
    {
      key: "requester",
      header: "Requested by",
      render: (r) => <span className="text-ink-soft">{r.requested_by || "—"}</span>,
    },
    {
      key: "warehouse",
      header: "Warehouse",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="text-ink-soft">{r.warehouse_name ?? "—"}</span>,
    },
    {
      key: "lines",
      header: "Lines",
      align: "right",
      render: (r) => <span className="tnum text-muted">{r.line_count}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (r) => (
        <div className="flex flex-col items-start gap-1">
          <RequisitionStatusPill status={r.status} />
          {r.converted_po_number ? <span className="text-xs text-muted tnum">{r.converted_po_number}</span> : null}
        </div>
      ),
    },
    {
      key: "needed",
      header: "Needed by",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum text-muted">{formatDate(r.needed_by)}</span>,
    },
  ];

  const filtersActive = debouncedSearch !== "" || status !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Procurement"
        title="Purchase Requisitions"
        actions={
          <Can permission={WRITE} scope="OWN">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/purchase-requisitions/new")}>
              New requisition
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-4 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search requisitions"
            placeholder="Search by number or requester…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
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
          <div className="flex flex-col gap-3 p-3" role="status" aria-label="Loading requisitions">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <LoadError error={state.error} noun="requisitions" onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(r) => r.id}
              caption="Purchase requisitions"
              onRowClick={(r) => navigate(`/purchase-requisitions/${r.id}`)}
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title={filtersActive ? "No requisitions match these filters" : "No requisitions yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the search."
                      : "Raise one to ask for stock before a purchase order exists."
                  }
                  action={
                    filtersActive ? null : (
                      <Can permission={WRITE} scope="OWN">
                        <Button size="sm" onClick={() => navigate("/purchase-requisitions/new")}>
                          New requisition
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="requisitions"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </div>
  );
}
