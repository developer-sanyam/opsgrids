import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { RollOut } from "@/api/trackingTypes";
import { formatQty } from "@/lib/quantity";
import { Button, EmptyState } from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { LayersIcon } from "@opsgrids/ui";
import { RollStateBadge } from "./badges";
import { CursorTable } from "./CursorTable";
import type { CursorList } from "./useCursorList";

const LINK = "font-semibold text-accent hover:text-accent-strong transition-colors";

interface RollsTableProps {
  list: CursorList<RollOut>;
  hideItem?: boolean | undefined;
  filtered?: boolean | undefined;
  onCreate?: (() => void) | undefined;
  /** Tick boxes for choosing rolls to merge. Omitted without `operations.inventory.roll.write`. */
  selection?: TableSelection<RollOut> | undefined;
  bulkActions?: ReactNode;
  toolbar?: ReactNode;
}

/** Rolls, newest first. A row opens the roll's history and parent/children trace. */
export function RollsTable({ list, hideItem, filtered, onCreate, selection, bulkActions, toolbar }: RollsTableProps) {
  const navigate = useNavigate();
  const columns: Column<RollOut>[] = [
    {
      key: "roll",
      header: "Roll",
      render: (r) => (
        <Link to={`/inventory/rolls/${r.id}`} className={`tnum ${LINK}`}>
          {r.roll_no}
        </Link>
      ),
    },
    ...(hideItem
      ? []
      : [
          {
            key: "item",
            header: "Item",
            render: (r: RollOut) => <span className="tnum text-ink-soft">{r.sku}</span>,
          },
        ]),
    {
      key: "lot",
      header: "Lot",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) =>
        r.lot_id ? (
          <Link to={`/inventory/lots/${r.lot_id}`} className={LINK}>
            {r.lot_no}
          </Link>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    { key: "state", header: "State", render: (r) => <RollStateBadge state={r.state} /> },
    {
      key: "shade",
      header: "Shade · grade",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (r) => <span className="text-ink-soft">{[r.shade, r.grade].filter(Boolean).join(" · ") || "—"}</span>,
    },
    {
      key: "size",
      header: "Width · GSM",
      cellClassName: "hidden xl:table-cell",
      headerClassName: "hidden xl:table-cell",
      render: (r) => (
        <span className="tnum text-ink-soft">
          {r.width_mm ? `${r.width_mm} mm` : "—"} · {r.gsm ?? "—"}
        </span>
      ),
    },
    {
      key: "on_hand",
      header: "On hand",
      align: "right",
      render: (r) => (
        <span className="tnum font-semibold text-ink whitespace-nowrap">
          {formatQty(r.on_hand_qty)} <span className="text-xs font-normal text-muted">{r.uom}</span>
          {r.secondary_qty && r.secondary_uom ? (
            <span className="block text-xs font-normal text-muted">
              {formatQty(r.secondary_qty)} {r.secondary_uom}
            </span>
          ) : null}
        </span>
      ),
    },
  ];

  return (
    <CursorTable
      list={list}
      columns={columns}
      rowKey={(r) => r.id}
      caption="Rolls"
      noun="rolls"
      errorTitle="Couldn't load rolls"
      toolbar={toolbar}
      onRowClick={(r) => navigate(`/inventory/rolls/${r.id}`)}
      selection={selection}
      bulkActions={bulkActions}
      emptyState={
        <EmptyState
          icon={<LayersIcon className="size-6" />}
          title={filtered ? "No rolls match these filters" : "No rolls yet"}
          description={
            filtered
              ? "Try a different state, shade or grade, or clear the search."
              : "A roll is one physical piece of fabric. Register one, then post opening stock into it."
          }
          action={
            !filtered && onCreate ? (
              <Button size="sm" onClick={onCreate}>
                Create roll
              </Button>
            ) : null
          }
        />
      }
    />
  );
}
