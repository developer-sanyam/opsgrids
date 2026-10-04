import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { LotOut } from "@/api/trackingTypes";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { Button, EmptyState } from "@/ui";
import type { Column } from "@/ui";
import { PackageIcon } from "@opsgrids/ui";
import { LotQualityBadge } from "./badges";
import { CursorTable } from "./CursorTable";
import type { CursorList } from "./useCursorList";

interface LotsTableProps {
  list: CursorList<LotOut>;
  hideItem?: boolean | undefined;
  filtered?: boolean | undefined;
  /** Shown in the empty state when the caller may register lots. */
  onCreate?: (() => void) | undefined;
  toolbar?: ReactNode;
}

/** Lots, newest first. A row opens the lot's genealogy and trace. */
export function LotsTable({ list, hideItem, filtered, onCreate, toolbar }: LotsTableProps) {
  const navigate = useNavigate();
  const columns: Column<LotOut>[] = [
    {
      key: "lot",
      header: "Lot",
      render: (l) => (
        <Link
          to={`/inventory/lots/${l.id}`}
          className="tnum font-semibold text-accent hover:text-accent-strong transition-colors"
        >
          {l.lot_no}
        </Link>
      ),
    },
    ...(hideItem
      ? []
      : [
          {
            key: "item",
            header: "Item",
            render: (l: LotOut) => <span className="tnum text-ink-soft">{l.sku}</span>,
          },
        ]),
    {
      key: "supplier",
      header: "Supplier lot",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (l) => <span className="text-ink-soft">{l.supplier_lot_no ?? "—"}</span>,
    },
    { key: "quality", header: "Quality", render: (l) => <LotQualityBadge status={l.quality_status} /> },
    {
      key: "received",
      header: "Received",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (l) => <span className="tnum text-muted">{formatDate(l.received_on)}</span>,
    },
    {
      key: "expires",
      header: "Expires",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (l) => <span className="tnum text-muted">{formatDate(l.expires_on)}</span>,
    },
    {
      key: "on_hand",
      header: "On hand",
      align: "right",
      render: (l) => (
        <span className="tnum font-semibold text-ink whitespace-nowrap">
          {formatQty(l.on_hand_qty)} <span className="text-xs font-normal text-muted">{l.uom}</span>
        </span>
      ),
    },
  ];

  return (
    <CursorTable
      list={list}
      columns={columns}
      rowKey={(l) => l.id}
      caption="Lots"
      noun="lots"
      errorTitle="Couldn't load lots"
      toolbar={toolbar}
      onRowClick={(l) => navigate(`/inventory/lots/${l.id}`)}
      emptyState={
        <EmptyState
          icon={<PackageIcon className="size-6" />}
          title={filtered ? "No lots match these filters" : "No lots yet"}
          description={
            filtered
              ? "Try a different quality status, or clear the search."
              : "A lot is one batch of an item. Create one, then post stock into it."
          }
          action={
            !filtered && onCreate ? (
              <Button size="sm" onClick={onCreate}>
                Create lot
              </Button>
            ) : null
          }
        />
      }
    />
  );
}
