import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { LedgerRowOut } from "@/api/trackingTypes";
import { formatDate } from "@/lib/format";
import { formatQty, qtyToMilli } from "@/lib/quantity";
import { ledgerRowLabel } from "@/lib/trackingLabels";
import { EmptyState } from "@/ui";
import type { Column } from "@/ui";
import { LayersIcon } from "@opsgrids/ui";
import { StockStatusBadge } from "./badges";
import { CursorTable } from "./CursorTable";
import type { CursorList } from "./useCursorList";

const LINK = "font-semibold text-accent hover:text-accent-strong transition-colors";

interface LedgerTableProps {
  list: CursorList<LedgerRowOut>;
  hideItem?: boolean | undefined;
  filtered?: boolean | undefined;
  toolbar?: ReactNode;
}

/**
 * The stock ledger, newest first. Append-only: there is no edit or delete anywhere on it, and the
 * empty state says what produces rows rather than inviting one to be added by hand.
 */
export function LedgerTable({ list, hideItem, filtered, toolbar }: LedgerTableProps) {
  const columns: Column<LedgerRowOut>[] = [
    {
      key: "date",
      header: "Date",
      render: (r) => <span className="tnum whitespace-nowrap text-muted">{formatDate(r.occurred_on)}</span>,
    },
    ...(hideItem
      ? []
      : [
          {
            key: "item",
            header: "Item",
            render: (r: LedgerRowOut) => (
              <Link to={`/inventory/${r.inventory_item_id}`} className={`tnum ${LINK}`}>
                {r.sku}
              </Link>
            ),
          },
        ]),
    {
      key: "operation",
      header: "Operation",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{ledgerRowLabel(r)}</span>
          {r.reason ? <span className="text-xs capitalize text-muted">{r.reason}</span> : null}
          {r.reference_type === "purchase_order" && r.reference_id ? (
            <Link to={`/purchase-orders/${r.reference_id}`} className={`text-xs ${LINK}`}>
              View purchase order
            </Link>
          ) : null}
        </div>
      ),
    },
    {
      key: "location",
      header: "Location",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="tnum text-ink-soft">{r.location_code}</span>,
    },
    {
      key: "status",
      header: "Status",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => <StockStatusBadge status={r.status} />,
    },
    {
      key: "tracked",
      header: "Lot / roll",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) =>
        r.lot_id || r.roll_id ? (
          <span className="flex gap-3">
            {r.lot_id ? (
              <Link to={`/inventory/lots/${r.lot_id}`} className={LINK}>
                Lot
              </Link>
            ) : null}
            {r.roll_id ? (
              <Link to={`/inventory/rolls/${r.roll_id}`} className={LINK}>
                Roll
              </Link>
            ) : null}
          </span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "change",
      header: "Change",
      align: "right",
      render: (r) => {
        const negative = qtyToMilli(r.quantity) < 0;
        return (
          <span className={`tnum whitespace-nowrap font-semibold ${negative ? "text-negative" : "text-present"}`}>
            {negative ? "" : "+"}
            {formatQty(r.quantity)} <span className="text-xs font-normal text-muted">{r.uom}</span>
          </span>
        );
      },
    },
    {
      key: "after",
      header: "Balance after",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => <span className="tnum text-ink-soft">{formatQty(r.balance_after)}</span>,
    },
    {
      key: "note",
      header: "Note",
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      mobile: "hidden",
      render: (r) => <span className="text-ink-soft">{r.note || "—"}</span>,
    },
  ];

  return (
    <CursorTable
      list={list}
      columns={columns}
      rowKey={(r) => r.id}
      caption="Stock ledger"
      noun="ledger rows"
      errorTitle="Couldn't load the ledger"
      toolbar={toolbar}
      emptyState={
        <EmptyState
          icon={<LayersIcon className="size-6" />}
          title={filtered ? "No ledger rows match these filters" : "The ledger is empty"}
          description={
            filtered
              ? "Try a wider date range, or clear the filters."
              : "Rows appear here as stock is received, moved, adjusted or counted. The ledger can't be edited."
          }
        />
      }
    />
  );
}
