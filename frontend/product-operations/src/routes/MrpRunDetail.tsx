import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import {
  getMrpRun,
  getMrpShortage,
  listMrpDemands,
  listMrpShortages,
  requestMrpRequisition,
} from "@/api/client";
import type {
  MrpDemandOut,
  MrpDemandSource,
  MrpRunOut,
  MrpShortageAction,
  MrpShortageOut,
  RequisitionRequestOut,
} from "@/api/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { BackLink } from "@/routes/manufacturing/BackLink";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Drawer,
  EmptyState,
  ErrorState,
  InlineAlert,
  MrpRunStatusPill,
  PageHeader,
  Pagination,
  Pill,
  SearchInput,
  Skeleton,
  Tag,
  TextArea,
} from "@/ui";
import type { Column } from "@/ui";
import { CheckCircleIcon, ClipboardListIcon, useToast } from "@opsgrids/ui";

const SOURCE_LABEL: Record<MrpDemandSource, string> = {
  SALES_ORDER: "Sales order",
  PRODUCTION_ORDER: "Production order",
  MANUAL: "Manual",
};

type View = "shortages" | "demand";
type ActionFilter = MrpShortageAction | "all";
type RequestedFilter = "all" | "pending" | "requested";

const ACTION_FILTERS: { value: ActionFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "BUY", label: "Buy" },
  { value: "MAKE", label: "Make" },
];
const REQUESTED_FILTERS: { value: RequestedFilter; label: string }[] = [
  { value: "all", label: "Any requisition state" },
  { value: "pending", label: "Not requested" },
  { value: "requested", label: "Requested" },
];

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="text-sm text-ink tnum">{children}</dd>
    </div>
  );
}

function qty(value: string, uom: string) {
  return (
    <span className="tnum">
      {formatQty(value)} {uom}
    </span>
  );
}

/**
 * One MRP run: its facts, then the shortage table (what to buy and what to make, with the supply
 * it was netted against), the demand it planned from, and the "request requisition" action for the
 * purchased-item shortages. A queued run is polled until the worker finishes it. Everything shown
 * is the server's stored result; no figure is recomputed here, and quantities are never parsed.
 */
export function MrpRunDetailPage({ pollMs = 3000 }: { pollMs?: number }) {
  const { runId = "" } = useParams();
  const toast = useToast();
  const canRequest = useCan("operations.manufacturing.mrp.requisition", "ORG");

  const loaded = useAsync(() => getMrpRun(runId), [runId]);
  const [run, setRun] = useState<MrpRunOut | null>(null);
  useEffect(() => {
    if (loaded.data) setRun(loaded.data);
  }, [loaded.data]);

  // A queued run changes under us: poll quietly (no loading flash) until it is no longer queued.
  const queued = run?.status === "QUEUED";
  useEffect(() => {
    if (!queued) return undefined;
    const timer = setInterval(() => {
      getMrpRun(runId)
        .then((fresh) => setRun(fresh))
        .catch(() => undefined);
    }, pollMs);
    return () => clearInterval(timer);
  }, [queued, runId, pollMs]);

  const [view, setView] = useState<View>("shortages");

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink to="/manufacturing/mrp">All planning runs</BackLink>

      {loaded.status === "loading" && run === null ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading run">
          <Skeleton className="h-10 w-64 rounded-tile" />
          <Skeleton className="h-28 w-full rounded-card" />
        </div>
      ) : null}
      {loaded.status === "error" && run === null ? (
        <ErrorState title="Couldn't load this run" message={problemMessage(loaded.error)} onRetry={loaded.reload} />
      ) : null}

      {run !== null ? (
        <>
          <PageHeader
            eyebrow="Material planning"
            title={run.number}
            description={run.notes !== "" ? run.notes : `Planned for ${formatDate(run.plan_date)}.`}
            actions={<MrpRunStatusPill status={run.status} />}
          />

          <Card className="p-4 sm:p-6">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <Fact label="Plan date">{formatDate(run.plan_date)}</Fact>
              <Fact label="Planned from">{run.sources.map((s) => SOURCE_LABEL[s]).join(", ")}</Fact>
              <Fact label="Demand lines">{run.status === "COMPLETED" ? run.demand_count : "—"}</Fact>
              <Fact label="Items planned">{run.status === "COMPLETED" ? run.items_planned : "—"}</Fact>
              <Fact label="Shortages">{run.status === "COMPLETED" ? run.shortage_count : "—"}</Fact>
              <Fact label="Started">{formatDateTime(run.created_at)}</Fact>
            </dl>
          </Card>

          {run.status === "QUEUED" ? (
            <InlineAlert tone="info" title="Waiting for the worker" live="off">
              This run is large, so it is computed in the background. This page updates when it is done.
            </InlineAlert>
          ) : null}
          {run.status === "FAILED" ? (
            <InlineAlert tone="danger" title="This run could not be computed">
              {run.error !== "" ? run.error : "No reason was recorded."}
            </InlineAlert>
          ) : null}

          {run.status === "COMPLETED" ? (
            <>
              <div className="-mx-1 flex items-center gap-2 overflow-x-auto px-1">
                <Pill active={view === "shortages"} onClick={() => setView("shortages")}>
                  Shortages
                </Pill>
                <Pill active={view === "demand"} onClick={() => setView("demand")}>
                  Demand
                </Pill>
              </div>
              {view === "shortages" ? (
                <ShortagesSection run={run} canRequest={canRequest} onRequested={(r) => toast.success(requestedMessage(r))} />
              ) : (
                <DemandSection runId={run.id} />
              )}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function requestedMessage(r: RequisitionRequestOut): string {
  return `Requisition requested for ${r.lines.length} ${r.lines.length === 1 ? "item" : "items"}.`;
}

/* -------------------------------- shortages -------------------------------- */

function ShortagesSection({
  run,
  canRequest,
  onRequested,
}: {
  run: MrpRunOut;
  canRequest: boolean;
  onRequested: (r: RequisitionRequestOut) => void;
}) {
  const [action, setAction] = useState<ActionFilter>("all");
  const [requestedFilter, setRequestedFilter] = useState<RequestedFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [open, setOpen] = useState<MrpShortageOut | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [last, setLast] = useState<RequisitionRequestOut | null>(null);

  const state = useAsync(
    () =>
      listMrpShortages(run.id, {
        action,
        ...(requestedFilter === "all" ? {} : { requested: requestedFilter === "requested" }),
        q: search,
        page,
        size,
      }),
    [run.id, action, requestedFilter, search, page, size],
  );

  // The purchased shortages still waiting for a requisition: what "Request requisition" would send.
  const pending = useAsync(
    () => listMrpShortages(run.id, { action: "BUY", requested: false, size: 100 }),
    [run.id, last],
  );

  const columns: Column<MrpShortageOut>[] = [
    {
      key: "item",
      header: "Item",
      render: (s) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{s.sku}</span>
          <span className="text-xs text-muted">{s.name}</span>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      render: (s) => <Tag tone={s.action === "BUY" ? "info" : "neutral"}>{s.action === "BUY" ? "Buy" : "Make"}</Tag>,
    },
    { key: "gross", header: "Required", align: "right", render: (s) => qty(s.gross, s.uom) },
    {
      key: "onhand",
      header: "On hand",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (s) => <span className="text-muted">{qty(s.on_hand, s.uom)}</span>,
    },
    {
      key: "reserved",
      header: "Reserved",
      align: "right",
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      render: (s) => <span className="text-muted">{qty(s.reserved, s.uom)}</span>,
    },
    {
      key: "po",
      header: "On order",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (s) => <span className="text-muted">{qty(s.open_po, s.uom)}</span>,
    },
    {
      key: "production",
      header: "In production",
      align: "right",
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      render: (s) => <span className="text-muted">{qty(s.open_production, s.uom)}</span>,
    },
    {
      key: "shortage",
      header: "Shortage",
      align: "right",
      render: (s) => <span className="font-semibold text-ink">{qty(s.shortage, s.uom)}</span>,
    },
    {
      key: "needby",
      header: "Needed by",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="text-muted tnum">{formatDate(s.need_by)}</span>,
    },
    {
      key: "req",
      header: "Requisition",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (s) =>
        s.requisition_request_id !== null ? (
          <Tag tone="positive">Requested</Tag>
        ) : s.action === "BUY" ? (
          <span className="text-muted">Not yet</span>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
  ];

  const filtered = action !== "all" || requestedFilter !== "all" || search !== "";
  const pendingCount = pending.status === "success" ? pending.data.total : 0;

  return (
    <>
      {last !== null ? (
        <InlineAlert tone="success" title={requestedMessage(last)}>
          Request {last.request_id.slice(0, 8)}: a draft purchase requisition is created from it; find it under Purchase requisitions.
        </InlineAlert>
      ) : null}

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <SearchInput
            label="Search shortages"
            placeholder="Search by SKU or item name…"
            value={search}
            onDebouncedChange={(q) => {
              setSearch(q);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <Can permission="operations.manufacturing.mrp.requisition" scope="ORG">
            <Button
              leadingIcon={<CheckCircleIcon className="size-4" />}
              disabled={pendingCount === 0}
              onClick={() => setRequesting(true)}
            >
              {pendingCount > 0 ? `Request requisition (${pendingCount})` : "Request requisition"}
            </Button>
          </Can>
        </div>
        <div className="-mx-1 flex flex-wrap items-center gap-2 px-1">
          {ACTION_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={action === f.value}
              onClick={() => {
                setAction(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
          <span className="mx-1 h-4 w-px bg-hairline" aria-hidden="true" />
          {REQUESTED_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={requestedFilter === f.value}
              onClick={() => {
                setRequestedFilter(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading shortages">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load the shortages" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(s) => s.id}
              caption="Shortages"
              onRowClick={(s) => setOpen(s)}
              emptyState={
                <EmptyState
                  icon={<CheckCircleIcon className="size-6" />}
                  title={filtered ? "No shortages match" : "Nothing is short"}
                  description={
                    filtered
                      ? "Try a different filter or search."
                      : "Stock, open purchase orders and open production cover everything this run planned."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="shortages"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {open !== null ? <PeggingDrawer runId={run.id} shortage={open} onClose={() => setOpen(null)} /> : null}

      {requesting && canRequest ? (
        <RequestRequisitionDialog
          runId={run.id}
          number={run.number}
          lines={pending.status === "success" ? pending.data.items : []}
          total={pendingCount}
          onClose={() => setRequesting(false)}
          onDone={(result) => {
            setRequesting(false);
            setLast(result);
            state.reload();
            onRequested(result);
          }}
        />
      ) : null}
    </>
  );
}

/* --------------------------------- pegging --------------------------------- */

function PeggingDrawer({
  runId,
  shortage,
  onClose,
}: {
  runId: string;
  shortage: MrpShortageOut;
  onClose: () => void;
}) {
  const detail = useAsync(() => getMrpShortage(runId, shortage.id), [runId, shortage.id]);
  return (
    <Drawer
      open
      onClose={onClose}
      dismissOnBackdrop
      size="lg"
      title={`${shortage.sku} — ${shortage.action === "BUY" ? "buy" : "make"} ${formatQty(shortage.shortage)} ${shortage.uom}`}
      description={`Required ${formatQty(shortage.gross)} ${shortage.uom}; supply ${formatQty(shortage.supply)} ${shortage.uom} (available ${formatQty(shortage.available)}, on order ${formatQty(shortage.open_po)}, in production ${formatQty(shortage.open_production)}).`}
    >
      <h3 className="mb-3 text-sm font-semibold text-ink">What needs it</h3>
      {detail.status === "loading" ? (
        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading pegging">
          <Skeleton className="h-10 w-full rounded-tile" />
          <Skeleton className="h-10 w-full rounded-tile" />
        </div>
      ) : null}
      {detail.status === "error" ? (
        <ErrorState title="Couldn't load the pegging" message={problemMessage(detail.error)} onRetry={detail.reload} />
      ) : null}
      {detail.status === "success" ? (
        <DataTable
          caption="Demand behind this shortage"
          rows={detail.data.lines}
          rowKey={(l) => `${l.source_type}:${l.source_ref}:${l.parent_item_id ?? "direct"}`}
          columns={[
            {
              key: "source",
              header: "Demand",
              render: (l) => (
                <div className="flex flex-col leading-tight">
                  <span className="font-semibold text-ink">{l.source_ref}</span>
                  <span className="text-xs text-muted">{SOURCE_LABEL[l.source_type]}</span>
                </div>
              ),
            },
            {
              key: "via",
              header: "Through",
              render: (l) => <span className="text-muted">{l.parent_sku ?? "Directly"}</span>,
            },
            {
              key: "qty",
              header: "Quantity",
              align: "right",
              render: (l) => qty(l.quantity, shortage.uom),
            },
            {
              key: "due",
              header: "Needed by",
              render: (l) => <span className="text-muted tnum">{formatDate(l.due_date)}</span>,
            },
          ]}
          emptyState={<EmptyState title="No demand recorded" description="This shortage has no pegged demand." />}
        />
      ) : null}
    </Drawer>
  );
}

/* ------------------------------ the requisition ----------------------------- */

function RequestRequisitionDialog({
  runId,
  number,
  lines,
  total,
  onClose,
  onDone,
}: {
  runId: string;
  number: string;
  lines: MrpShortageOut[];
  total: number;
  onClose: () => void;
  onDone: (result: RequisitionRequestOut) => void;
}) {
  const [note, setNote] = useState("");
  const keyFor = useIdempotencyKeys();
  const request = useMutation(
    (input: { note: string }) =>
      requestMrpRequisition(runId, input.note.trim() !== "" ? { note: input.note.trim() } : {}, {
        idempotencyKey: keyFor(input),
      }),
    { onSuccess: (result) => onDone(result) },
  );

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      onConfirm={() => void request.mutate({ note })}
      title="Request a purchase requisition"
      body={`Ask purchasing to requisition the ${total} purchased ${total === 1 ? "item" : "items"} that ${number} found short. Items you make are not included.`}
      confirmLabel="Request requisition"
      pendingLabel="Requesting…"
      tone="primary"
      pending={request.pending}
      {...(request.error ? { error: problemMessage(request.error) } : {})}
    >
      <ul className="mb-4 flex max-h-48 flex-col gap-1 overflow-y-auto text-sm" aria-label="Items to requisition">
        {lines.map((l) => (
          <li key={l.id} className="flex justify-between gap-4 tnum">
            <span className="font-semibold text-ink">{l.sku}</span>
            <span className="text-muted">
              {formatQty(l.shortage)} {l.uom}
            </span>
          </li>
        ))}
        {total > lines.length ? <li className="text-muted">…and {total - lines.length} more.</li> : null}
      </ul>
      <TextArea label="Note for purchasing" value={note} onChange={(e) => setNote(e.target.value)} optional />
    </ConfirmDialog>
  );
}

/* ---------------------------------- demand ---------------------------------- */

function DemandSection({ runId }: { runId: string }) {
  const [source, setSource] = useState<MrpDemandSource | "all">("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const state = useAsync(() => listMrpDemands(runId, { source_type: source, page, size }), [runId, source, page, size]);

  const columns: Column<MrpDemandOut>[] = [
    {
      key: "source",
      header: "Demand",
      render: (d) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{d.source_ref}</span>
          <span className="text-xs text-muted">{SOURCE_LABEL[d.source_type]}</span>
        </div>
      ),
    },
    {
      key: "item",
      header: "Item",
      render: (d) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{d.sku}</span>
          <span className="text-xs text-muted">{d.name}</span>
        </div>
      ),
    },
    { key: "requested", header: "Requested", align: "right", render: (d) => qty(d.requested, d.uom) },
    {
      key: "reserved",
      header: "Already reserved",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (d) => <span className="text-muted">{qty(d.reserved, d.uom)}</span>,
    },
    { key: "open", header: "Planned", align: "right", render: (d) => <span className="font-semibold">{qty(d.quantity, d.uom)}</span> },
    {
      key: "due",
      header: "Needed by",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (d) => <span className="text-muted tnum">{formatDate(d.due_date)}</span>,
    },
  ];

  return (
    <>
      <div className="-mx-1 flex items-center gap-2 overflow-x-auto px-1">
        {(["all", "SALES_ORDER", "PRODUCTION_ORDER", "MANUAL"] as const).map((s) => (
          <Pill
            key={s}
            active={source === s}
            onClick={() => {
              setSource(s);
              setPage(1);
            }}
          >
            {s === "all" ? "All sources" : SOURCE_LABEL[s]}
          </Pill>
        ))}
      </div>
      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading demand">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load the demand" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(d) => d.id}
              caption="Demand this run planned from"
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title="No demand"
                  description="Nothing open was found in the chosen sources when this run was computed."
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="demand lines"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </>
  );
}
