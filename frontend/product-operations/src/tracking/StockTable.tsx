import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { StockRowOut } from "@/api/trackingTypes";
import { useCan } from "@opsgrids/client";
import { formatQty } from "@/lib/quantity";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { isOnSiteRow } from "@/lib/trackingLabels";
import { EmptyState, RowActions } from "@/ui";
import type { Column } from "@/ui";
import { LayersIcon } from "@opsgrids/ui";
import { StockStatusBadge } from "./badges";
import { CursorTable } from "./CursorTable";
import type { CursorList } from "./useCursorList";

const LINK = "font-semibold text-accent hover:text-accent-strong transition-colors";

interface StockTableProps {
  list: CursorList<StockRowOut>;
  /** Hide the item column where the whole table is one item's. */
  hideItem?: boolean | undefined;
  /** Whether any filter is narrowing the list, so the empty state says the right thing. */
  filtered?: boolean | undefined;
  onTransfer: (row: OnSiteRow) => void;
  onChangeStatus: (row: OnSiteRow) => void;
  emptyAction?: ReactNode;
  toolbar?: ReactNode;
}

/**
 * Stock by bucket (item × location × status × lot × roll), with the two operations that act on a
 * bucket — a bin-to-bin transfer and a status change — gated on `operations.inventory.stock.transfer`.
 * Buckets in transit or at a job worker offer neither: those need documents (transfer orders,
 * job-work orders) that arrive in later phases.
 */
export function StockTable({ list, hideItem, filtered, onTransfer, onChangeStatus, emptyAction, toolbar }: StockTableProps) {
  const canTransfer = useCan("operations.inventory.stock.transfer", "ORG");
  const columns: Column<StockRowOut>[] = [
    ...(hideItem
      ? []
      : [
          {
            key: "item",
            header: "Item",
            render: (r: StockRowOut) => (
              <Link to={`/inventory/${r.inventory_item_id}`} className="flex flex-col leading-tight">
                <span className="tnum font-semibold text-ink">{r.sku}</span>
                <span className="text-xs text-muted">{r.item_name}</span>
              </Link>
            ),
          },
        ]),
    {
      key: "location",
      header: "Location",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="tnum font-semibold text-ink">{r.location_code}</span>
          <span className="text-xs text-muted">{r.warehouse_code}</span>
        </div>
      ),
    },
    { key: "status", header: "Status", render: (r) => <StockStatusBadge status={r.status} /> },
    {
      key: "lot",
      header: "Lot",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      mobile: "field",
      render: (r) =>
        r.lot_id ? (
          <Link to={`/inventory/lots/${r.lot_id}`} className={LINK}>
            {r.lot_no}
          </Link>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "roll",
      header: "Roll",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      mobile: "field",
      render: (r) =>
        r.roll_id ? (
          <Link to={`/inventory/rolls/${r.roll_id}`} className={LINK}>
            {r.roll_no}
          </Link>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "on_hand",
      header: "On hand",
      align: "right",
      render: (r) => (
        <span className="tnum font-semibold text-ink whitespace-nowrap">
          {formatQty(r.on_hand_qty)} <span className="text-xs font-normal text-muted">{r.uom}</span>
        </span>
      ),
    },
    {
      key: "reserved",
      header: "Reserved",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum text-muted">{formatQty(r.reserved_qty)}</span>,
    },
    {
      key: "available",
      header: "Available",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum text-ink-soft">{formatQty(r.available_qty)}</span>,
    },
    ...(canTransfer
      ? [
          {
            key: "actions",
            header: "",
            align: "right" as const,
            mobile: "actions" as const,
            render: (r: StockRowOut) => (
              <RowActions
                label={`${r.sku} at ${r.location_code}`}
                actions={[
                  {
                    label: "Transfer",
                    onSelect: () => isOnSiteRow(r) && onTransfer(r),
                    disabled: !isOnSiteRow(r),
                    disabledReason: "Stock in transit or at a job worker moves through its own document.",
                  },
                  {
                    label: "Change status",
                    onSelect: () => isOnSiteRow(r) && onChangeStatus(r),
                    disabled: !isOnSiteRow(r),
                    disabledReason: "Stock in transit or at a job worker moves through its own document.",
                  },
                ]}
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <CursorTable
      list={list}
      columns={columns}
      rowKey={(r) => r.id}
      caption="Stock by location"
      noun="stock rows"
      errorTitle="Couldn't load stock"
      toolbar={toolbar}
      emptyState={
        <EmptyState
          icon={<LayersIcon className="size-6" />}
          title={filtered ? "No stock matches these filters" : "No stock on the books yet"}
          description={
            filtered
              ? "Try a different location or status, or clear the filters."
              : "Post opening stock to bring existing stock into a location."
          }
          action={filtered ? null : emptyAction}
        />
      }
    />
  );
}
