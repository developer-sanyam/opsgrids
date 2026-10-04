import { useState } from "react";

import { listReservations } from "@/api/movements";
import type { ReservationDetailOut, ReservationStatus } from "@/api/movementTypes";
import { Can, problemMessage, useCan } from "@opsgrids/client";
import { formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { ReleaseReservationDialog } from "@/forms/ReleaseReservationDialog";
import { ReserveStockDialog } from "@/forms/ReserveStockDialog";
import { LoadMoreFooter } from "@/movements/LoadMoreFooter";
import { MovementItemPicker } from "@/movements/MovementItemPicker";
import { useCursorList } from "@/tracking/useCursorList";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  ReservationStatusPill,
  RowActions,
  Select,
} from "@/ui";
import type { Column } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { LayersIcon, useToast } from "@opsgrids/ui";

type StatusFilter = ReservationStatus | "all";
type ReferenceFilter = "all" | "order" | "production_order" | "manual";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "released", label: "Released" },
  { value: "consumed", label: "Used" },
  { value: "transferred", label: "Moved" },
];

const REFERENCE_FILTERS: { value: ReferenceFilter; label: string }[] = [
  { value: "all", label: "Any reference" },
  { value: "order", label: "Sales order" },
  { value: "production_order", label: "Production order" },
  { value: "manual", label: "Manual hold" },
];

const REFERENCE_NAME: Record<string, string> = {
  order: "Sales order",
  production_order: "Production order",
  manual: "Manual hold",
};

/** "Sales order · 5b6f3c1e", or just "Manual hold" when no document is named. */
function referenceText(r: ReservationDetailOut): string {
  const name = REFERENCE_NAME[r.reference_type ?? "manual"] ?? "Manual hold";
  return r.reference_id === null ? name : `${name} · ${r.reference_id.slice(0, 8)}`;
}

/**
 * Reservations (inventory Phase 3): stock promised to a demand without moving it. A cursor-paged
 * list with status, reference and item filters; "Reserve stock" and the per-row release are gated
 * on `operations.inventory.stock.reserve` — presentation only, the server refuses the call anyway.
 */
export function ReservationsPage() {
  const canReserve = useCan("operations.inventory.stock.reserve", "ORG");
  const toast = useToast();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [reference, setReference] = useState<ReferenceFilter>("all");
  const [itemOption, setItemOption] = useState<ComboboxOption | null>(null);
  const [reserving, setReserving] = useState(false);
  const [releasing, setReleasing] = useState<ReservationDetailOut | null>(null);

  const list = useCursorList(
    (cursor, signal) =>
      listReservations({
        status: status === "all" ? undefined : status,
        reference_type: reference === "all" ? undefined : reference,
        item_id: itemOption?.id,
        cursor,
        limit: 25,
        signal,
      }),
    [status, reference, itemOption?.id],
  );

  const filtersActive = status !== "all" || reference !== "all" || itemOption !== null;

  const columns: Column<ReservationDetailOut>[] = [
    {
      key: "item",
      header: "Item",
      mobile: "title",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{r.sku}</span>
          <span className="text-xs text-muted">{r.item_name}</span>
        </div>
      ),
    },
    {
      key: "location",
      header: "Location",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="text-ink-soft">{r.location_code}</span>,
    },
    {
      key: "quantity",
      header: "Quantity",
      align: "right",
      render: (r) => (
        <span className="tnum text-ink-soft">
          {formatQty(r.quantity)} {r.uom}
        </span>
      ),
    },
    {
      key: "remaining",
      header: "Remaining",
      align: "right",
      render: (r) => (
        <span className="tnum font-semibold text-ink">
          {formatQty(r.remaining)} {r.uom}
        </span>
      ),
    },
    {
      key: "reference",
      header: "Reference",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => <span className="text-ink-soft">{referenceText(r)}</span>,
    },
    {
      key: "expires",
      header: "Expires",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) =>
        r.expired_at !== null ? (
          <span className="text-muted">Expired {formatDateTime(r.expired_at)}</span>
        ) : r.expires_at !== null ? (
          <span className="tnum text-ink-soft">{formatDateTime(r.expires_at)}</span>
        ) : (
          <span className="text-faint">No expiry</span>
        ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (r) => <ReservationStatusPill status={r.status} expired={r.expired_at !== null} />,
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      mobile: "actions",
      render: (r) => (
        <RowActions
          label={`Reservation of ${r.sku}`}
          actions={[
            {
              label: "Release",
              onSelect: () => setReleasing(r),
              hidden: !canReserve || r.status !== "active",
            },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Reservations"
        description="Stock promised to an order, a production run or a hand-made hold. Reserved stock stays on hand, but it can't be promised twice."
        actions={
          <Can permission="operations.inventory.stock.reserve" scope="ORG">
            <Button onClick={() => setReserving(true)}>Reserve stock</Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Select
            label="Reference"
            value={reference}
            onChange={(e) => setReference(e.target.value as ReferenceFilter)}
            options={REFERENCE_FILTERS}
          />
          <MovementItemPicker label="Item" value={itemOption} onChange={setItemOption} onPick={() => undefined} />
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        <DataTable
          columns={columns}
          rows={list.items}
          rowKey={(r) => r.id}
          caption="Reservations"
          mobileCard="auto"
          loading={list.status === "loading"}
          loadingRows={6}
          errorState={
            list.status === "error" ? (
              <ErrorState
                title="Couldn't load reservations"
                message={problemMessage(list.error)}
                onRetry={list.reload}
              />
            ) : undefined
          }
          emptyState={
            <EmptyState
              icon={<LayersIcon className="size-6" />}
              title={filtersActive ? "No reservations match these filters" : "No reservations yet"}
              description={
                filtersActive
                  ? "Try a different status or reference, or clear the item."
                  : "Reserve stock to hold it for an order or a production run."
              }
            />
          }
        />
        {list.status === "ready" ? (
          <LoadMoreFooter
            loaded={list.items.length}
            hasMore={list.hasMore}
            loading={list.loadingMore}
            error={list.moreError}
            onLoadMore={list.loadMore}
            noun="reservations"
          />
        ) : null}
      </Card>

      {reserving ? (
        <Can permission="operations.inventory.stock.reserve" scope="ORG">
          <ReserveStockDialog
            onClose={() => setReserving(false)}
            onReserved={(saved) => {
              setReserving(false);
              toast.success(`Reserved ${saved.remaining} ${saved.uom} of ${saved.sku}.`);
              list.reload();
            }}
          />
        </Can>
      ) : null}

      {releasing ? (
        <Can permission="operations.inventory.stock.reserve" scope="ORG">
          <ReleaseReservationDialog
            reservation={releasing}
            onClose={() => setReleasing(null)}
            onReleased={(released) => {
              setReleasing(null);
              toast.success(
                released.status === "released"
                  ? `Released ${releasing.sku} — the stock is available again.`
                  : `Released part of ${releasing.sku}. ${released.remaining} ${releasing.uom} is still reserved.`,
              );
              list.reload();
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
