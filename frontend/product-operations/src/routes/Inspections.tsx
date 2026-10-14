import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listInspections } from "@/api/client";
import type { InspectionOut, InspectionStatus, QcKind } from "@/api/types";
import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import { OpenInspectionDialog } from "@/forms/OpenInspectionDialog";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  InspectionStatusPill,
  PageHeader,
  Pagination,
  Pill,
  Select,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { ShieldCheckIcon } from "@opsgrids/ui";

type StatusFilter = InspectionStatus | "all";
type KindFilter = QcKind | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PENDING", label: "Pending" },
  { value: "HOLD", label: "On hold" },
  { value: "REWORK", label: "Rework" },
  { value: "PASSED", label: "Passed" },
  { value: "PARTIAL_PASS", label: "Partial pass" },
  { value: "FAILED", label: "Failed" },
];

const KIND_OPTIONS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All kinds" },
  { value: "incoming", label: "Incoming" },
  { value: "in-process", label: "In-process" },
  { value: "finished", label: "Finished" },
];

const KIND_LABEL: Record<QcKind, string> = {
  incoming: "Incoming",
  "in-process": "In-process",
  finished: "Finished",
};

/**
 * Inspections — stock held for quality checks and the decisions made on it. Quantities are the
 * server's decimal strings, shown as sent; nothing here sums or converts one.
 */
export function InspectionsPage() {
  const navigate = useNavigate();
  const canInspect = useCan("operations.quality.inspection.inspect", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [kind, setKind] = useState<KindFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [opening, setOpening] = useState(false);

  const state = useAsync(() => listInspections({ status, kind, page, size }), [status, kind, page, size]);

  const columns: Column<InspectionOut>[] = [
    {
      key: "item",
      header: "Item",
      mobile: "title",
      render: (i) => (
        <div className="flex flex-col leading-tight">
          <Link to={`/quality/inspections/${i.id}`} className="font-semibold text-ink hover:underline">
            {i.item_sku || i.item_id.slice(0, 8)}
          </Link>
          <span className="text-xs text-muted">{i.item_name}</span>
        </div>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      mobile: "status",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (i) => <span className="text-muted">{KIND_LABEL[i.kind]}</span>,
    },
    {
      key: "location",
      header: "Location",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (i) => <span className="text-muted">{i.location_code || "—"}</span>,
    },
    {
      key: "quantity",
      header: "Quantity",
      align: "right",
      render: (i) => (
        <span className="tnum">
          {formatQty(i.quantity)} {i.uom}
        </span>
      ),
    },
    { key: "status", header: "Status", render: (i) => <InspectionStatusPill status={i.status} /> },
    {
      key: "opened",
      header: "Opened",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (i) => <span className="text-muted">{formatDate(i.created_at)}</span>,
    },
  ];

  const filtersActive = status !== "all" || kind !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Quality"
        title="Inspections"
        description="Hold stock aside, record what you measured, then release it or reject it."
        actions={canInspect ? <Button onClick={() => setOpening(true)}>New inspection</Button> : undefined}
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
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
        <div className="max-w-56">
          <Select
            label="Kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as KindFilter);
              setPage(1);
            }}
            options={KIND_OPTIONS}
          />
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load inspections" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(i) => i.id}
              caption="Inspections"
              emptyState={
                <EmptyState
                  icon={<ShieldCheckIcon className="size-6" />}
                  title={filtersActive ? "No inspections match these filters" : "No inspections yet"}
                  description={
                    filtersActive
                      ? "Try a different status or kind, or clear the filters."
                      : canInspect
                        ? "Open one to hold stock aside for a quality check."
                        : "An inspector can open one."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="inspections"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {opening ? (
        <Can permission="operations.quality.inspection.inspect" scope="ORG">
          <OpenInspectionDialog
            onClose={() => setOpening(false)}
            onOpened={(created) => {
              setOpening(false);
              navigate(`/quality/inspections/${created.id}`);
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
