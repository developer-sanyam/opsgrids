import { useState } from "react";
import { Link } from "react-router-dom";

import { deliverShipment, failShipment, listShipments, markShipmentInTransit, returnShipment } from "@/api/client";
import type { ShipmentOut, ShipmentStatus } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import { DispatchOrderDialog } from "@/forms/DispatchOrderDialog";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  ShipmentStatusPill,
  Skeleton,
  TextArea,
} from "@/ui";
import type { Column } from "@/ui";
import { TruckIcon } from "@opsgrids/ui";

type StatusFilter = ShipmentStatus | "all";

/** `app.domain.SHIPMENT_TRANSITIONS`' exact vocabulary — the filter row offers exactly this set. */
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PICKUP_HANDOVER", label: "Pickup handover" },
  { value: "IN_TRANSIT", label: "In transit" },
  { value: "DELIVERED", label: "Delivered" },
  { value: "FAILED", label: "Failed" },
  { value: "RETURNED", label: "Returned" },
];

type RefusalKind = "fail" | "return";

function canMarkInTransit(s: ShipmentOut): boolean {
  return s.status === "PICKUP_HANDOVER" || s.status === "FAILED";
}

function canDeliver(s: ShipmentOut): boolean {
  return s.status === "IN_TRANSIT";
}

function canFail(s: ShipmentOut): boolean {
  return s.status === "PICKUP_HANDOVER" || s.status === "IN_TRANSIT";
}

function canReturn(s: ShipmentOut): boolean {
  return s.status === "PICKUP_HANDOVER" || s.status === "IN_TRANSIT" || s.status === "FAILED";
}

/**
 * The dispatcher's shipment board (Fulfilment plan F5): every carrier handover, its current
 * status and its own onward lifecycle — mark in transit (or retry a failed attempt), confirm
 * delivery, or record a failed attempt/return, each with a reason where the server requires one.
 * "New shipment" is the handover itself: it runs the same billing gate `OrderDetail`'s counter
 * path (`POST /orders/{id}/hand-over`) enforces, only for an order going out with a carrier.
 */
export function DispatchShipmentsPage() {
  const canWrite = useCan("operations.dispatch.shipment.write", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [dispatching, setDispatching] = useState(false);
  const [refusing, setRefusing] = useState<{ shipment: ShipmentOut; kind: RefusalKind } | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);

  const loader = () => listShipments({ status, page, size });
  const state = useAsync(loader, [status, page, size]);

  const inTransit = useMutation((s: ShipmentOut) => markShipmentInTransit(s.id), {
    onSuccess: () => state.reload(),
  });
  const deliver = useMutation((s: ShipmentOut) => deliverShipment(s.id), {
    onSuccess: () => state.reload(),
  });
  const refuse = useMutation(
    (input: { shipment: ShipmentOut; kind: RefusalKind; reason: string }) =>
      input.kind === "fail"
        ? failShipment(input.shipment.id, { reason: input.reason })
        : returnShipment(input.shipment.id, { reason: input.reason }),
    {
      onSuccess: () => {
        setRefusing(null);
        setReason("");
        state.reload();
      },
    },
  );

  function submitRefusal() {
    setReasonError(undefined);
    if (reason.trim().length < 1) {
      setReasonError(refusing?.kind === "fail" ? "Say why this delivery attempt failed." : "Say why this shipment is being returned.");
      return;
    }
    if (!refusing) return;
    void refuse.mutate({ shipment: refusing.shipment, kind: refusing.kind, reason: reason.trim() });
  }

  const columns: Column<ShipmentOut>[] = [
    {
      key: "order",
      header: "Order",
      mobile: "title",
      render: (s) => (
        <Link to={`/orders/${s.order_id}`} className="font-semibold text-ink tnum hover:text-accent transition-colors">
          {s.order_id.slice(0, 8)}
        </Link>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (s) => (
        <div className="flex flex-col gap-1">
          <ShipmentStatusPill status={s.status} />
          {s.status === "FAILED" && s.failure_reason ? (
            <span className="text-xs text-faint">{s.failure_reason}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "carrier",
      header: "Carrier",
      render: (s) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{s.carrier_name}</span>
          <span className="text-xs text-faint tnum">{s.tracking_reference ?? "No tracking reference yet"}</span>
        </div>
      ),
    },
    {
      key: "created",
      header: "Handed over",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (s) => <span className="tnum text-muted">{formatDate(s.created_at.slice(0, 10))}</span>,
    },
    {
      key: "actions",
      header: "",
      mobile: "actions",
      align: "right",
      render: (s) => (
        <RowActions
          label={`Shipment for order ${s.order_id.slice(0, 8)}`}
          actions={[
            {
              label: s.status === "FAILED" ? "Retry (in transit)" : "Mark in transit",
              onSelect: () => void inTransit.mutate(s),
              hidden: !canWrite || !canMarkInTransit(s),
            },
            {
              label: "Confirm delivery",
              onSelect: () => void deliver.mutate(s),
              hidden: !canWrite || !canDeliver(s),
            },
            {
              label: "Mark failed",
              tone: "danger",
              onSelect: () => setRefusing({ shipment: s, kind: "fail" }),
              hidden: !canWrite || !canFail(s),
            },
            {
              label: "Mark returned",
              tone: "danger",
              onSelect: () => setRefusing({ shipment: s, kind: "return" }),
              hidden: !canWrite || !canReturn(s),
            },
          ]}
        />
      ),
    },
  ];

  const filtersActive = status !== "all";
  const mutationError = inTransit.error ?? deliver.error;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Dispatch"
        description="Every carrier handover and where it stands — hand a ready order to a carrier, then track it through."
        actions={
          <Can permission="operations.dispatch.shipment.write" scope="ORG">
            <Button onClick={() => setDispatching(true)}>Hand to carrier</Button>
          </Can>
        }
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
      </Card>

      {mutationError ? <InlineAlert tone="danger" title={problemMessage(mutationError)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load shipments" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(s) => s.id}
              caption="Shipments"
              emptyState={
                <EmptyState
                  icon={<TruckIcon className="size-6" />}
                  title={filtersActive ? "No shipments match this filter" : "No shipments yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the filter."
                      : "A shipment appears here once a ready order is handed to a carrier."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="shipments"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {dispatching ? (
        <Can permission="operations.dispatch.shipment.write" scope="ORG">
          <DispatchOrderDialog
            onClose={() => setDispatching(false)}
            onDispatched={() => {
              setDispatching(false);
              state.reload();
            }}
          />
        </Can>
      ) : null}

      <ConfirmDialog
        open={refusing !== null}
        onClose={() => {
          setRefusing(null);
          setReason("");
          setReasonError(undefined);
          refuse.reset();
        }}
        onConfirm={submitRefusal}
        title={refusing?.kind === "fail" ? "Record a failed delivery attempt?" : "Record this shipment as returned?"}
        body={
          refusing?.kind === "fail"
            ? "The shipment moves to Failed — it can still be retried (in transit) or returned afterwards."
            : "The shipment moves to Returned. This is final."
        }
        confirmLabel={refusing?.kind === "fail" ? "Mark failed" : "Mark returned"}
        pendingLabel="Saving…"
        tone="danger"
        pending={refuse.pending}
        {...(refuse.error ? { error: problemMessage(refuse.error) } : {})}
      >
        <TextArea
          label="Reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          {...(reasonError !== undefined ? { error: reasonError } : {})}
        />
      </ConfirmDialog>
    </div>
  );
}
