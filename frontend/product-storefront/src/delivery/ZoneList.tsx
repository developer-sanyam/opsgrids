import { useState } from "react";

import { ApiError } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  Dialog,
  EmptyState,
  InlineAlert,
  MapPinIcon,
  Tag,
  cn,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { deleteZone } from "./api";
import { feeSentence, minimumOrderSentence, MODE_LABEL, zonePromiseSentence } from "./copy";
import { zoneCodeCount } from "./types";
import type { StoreOut, ZoneOut } from "./types";

/**
 * The row pieces live at module scope, **not inside `ZoneList`'s body**, and that is load-bearing
 * rather than tidiness.
 *
 * A component declared inside a render function is a new component *type* on every render, so
 * React unmounts and remounts its whole subtree each time the parent's state changes. Verified in
 * Chrome: with these nested, pressing "Postal codes" re-rendered the list, destroyed the button
 * that had focus, and left a keyboard user on `<body>` when the dialog closed — the dialog's
 * focus-return works perfectly and had nothing to restore to.
 */

/** What the buyer is told: the mode, whether it is switched on, and the promise. */
function ModeCell({ zone }: { zone: ZoneOut }) {
  const promise = zonePromiseSentence(zone);
  return (
    <div className="flex flex-col gap-1">
      <span className="flex flex-wrap items-center gap-1.5">
        <Tag tone={zone.mode === "UNSERVED" ? "neutral" : "info"}>{MODE_LABEL[zone.mode]}</Tag>
        {/* State is never colour alone: an off zone says "Switched off" in words. */}
        {zone.is_active ? null : <Tag tone="warning">Switched off</Tag>}
      </span>
      {promise !== null ? <span className="text-xs text-muted">{promise}</span> : null}
    </div>
  );
}

/** The display amounts. Never "charged" — nothing applies them to an order (ADR-0010 §8). */
function MoneyCell({ zone, currency }: { zone: ZoneOut; currency: string }) {
  const fee = feeSentence(zone.delivery_fee_minor, zone.free_delivery_above_minor, currency);
  const minimum = minimumOrderSentence(zone.min_order_minor, currency);
  if (fee === null && minimum === null) {
    return <span className="text-xs text-faint">Nothing set</span>;
  }
  return (
    <div className="flex flex-col gap-0.5 text-xs text-ink-soft">
      {fee !== null ? <span>{fee}</span> : null}
      {minimum !== null ? <span>{minimum}</span> : null}
    </div>
  );
}

interface RowActions {
  canWrite: boolean;
  onPostalCodes: (zone: ZoneOut) => void;
  onEdit: (zone: ZoneOut) => void;
  onDelete: (zone: ZoneOut) => void;
}

/**
 * The row's controls.
 *
 * A hidden control is presentation, not protection — the server refuses the write regardless
 * (ADR-0003 §3). The write controls are hidden only so a read-only merchant is not offered a form
 * that ends in a 403.
 */
function Actions({
  zone,
  actions,
  grow = false,
}: {
  zone: ZoneOut;
  actions: RowActions;
  grow?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2", grow ? "flex-wrap" : "justify-end")}>
      <Button size="sm" variant="outline" onClick={() => actions.onPostalCodes(zone)}>
        Postal codes
      </Button>
      {actions.canWrite ? (
        <>
          <Button size="sm" variant="ghost" onClick={() => actions.onEdit(zone)}>
            Edit
          </Button>
          <Button size="sm" variant="ghost" onClick={() => actions.onDelete(zone)}>
            Delete
          </Button>
        </>
      ) : null}
    </div>
  );
}

/**
 * The zones, as a real table from `md` up and a stacked list below it.
 *
 * Each row answers the three questions a merchant has about a zone in one glance — what buyers are
 * told, how many postal codes it covers, and whether it is switched on — and offers the two things
 * they do to it. The money column says "shown" rather than "charged", because nothing charges it
 * (ADR-0010 §8).
 */
export function ZoneList({
  zones,
  store,
  canWrite,
  onEdit,
  onPostalCodes,
  onCreate,
  onChanged,
}: {
  zones: readonly ZoneOut[];
  store: StoreOut;
  canWrite: boolean;
  onEdit: (zone: ZoneOut) => void;
  onPostalCodes: (zone: ZoneOut) => void;
  onCreate: () => void;
  onChanged: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState<ZoneOut | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function runDelete(zone: ZoneOut) {
    if (deleting) return;
    setDeleting(true);
    setFailure(null);
    try {
      await deleteZone(zone.id);
      setConfirming(null);
      onChanged(`${zone.name} deleted.`);
    } catch (error) {
      // ADR-0010 §9: the server refuses while postal codes are still mapped. Its sentence is the
      // specific one — it names the count — so it is shown verbatim rather than rewritten.
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Couldn't reach the server. The zone was not deleted.",
      );
    } finally {
      setDeleting(false);
    }
  }

  const actions: RowActions = {
    canWrite,
    onPostalCodes,
    onEdit,
    onDelete: setConfirming,
  };

  const columns: Column<ZoneOut>[] = [
    {
      key: "name",
      header: "Zone",
      render: (zone) => (
        <div className="flex flex-col gap-0.5">
          <span className="font-bold text-ink">{zone.name}</span>
          <span className="text-xs text-faint tnum">{zone.code}</span>
        </div>
      ),
    },
    { key: "mode", header: "Buyer is told", render: (zone) => <ModeCell zone={zone} /> },
    {
      key: "codes",
      header: "Postal codes",
      align: "right",
      cellClassName: "tnum",
      // Not `required` in the schema, so an absent count reads as "—" rather than a confident 0.
      render: (zone) => zoneCodeCount(zone) ?? "—",
    },
    {
      key: "money",
      header: "Shown to buyers",
      // Hidden below `lg`: three amounts in a narrow column wrap into porridge, and the stacked
      // layout below `md` shows them with their own heading.
      headerClassName: "hidden lg:table-cell",
      cellClassName: "hidden lg:table-cell",
      render: (zone) => <MoneyCell zone={zone} currency={store.currency} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      // `w-px` plus `whitespace-nowrap` is the shrink-to-content column: without it the table
      // hands this column the leftovers and the three controls wrap onto three lines. Measured in
      // Chrome at 1280 — jsdom has no layout engine and reported the same markup as fine.
      cellClassName: "w-px whitespace-nowrap",
      headerClassName: "w-px",
      render: (zone) => <Actions zone={zone} actions={actions} />,
    },
  ];

  return (
    <Card className="p-2 sm:p-4">
      {zones.length === 0 ? (
        <EmptyState
          icon={<MapPinIcon className="size-6" />}
          title="No delivery zones yet"
          description="A zone is a name, a delivery behaviour and a bag of postal codes. Until you add one, every buyer who asks is told the store doesn't deliver to them — and lands in your demand report."
          {...(canWrite ? { action: <Button onClick={onCreate}>Add your first zone</Button> } : {})}
        />
      ) : (
        <>
          {/* Five columns and three controls do not fit a phone. Measured in Chrome at 390px: the
              table's own `overflow-x-auto` pushed "Postal codes", "Edit" and "Delete" past the
              right edge — reachable only by scrolling sideways inside a card, which nothing on
              screen says is possible — and broke "Mumbai — central" across three lines. Below
              `md` the same rows are a list instead. Same data, same controls, same order; only
              the arrangement changes. */}
          <div className="hidden md:block">
            <DataTable
              columns={columns}
              rows={[...zones]}
              rowKey={(zone) => zone.id}
              caption="Delivery zones, with what each tells a buyer and how many postal codes it covers"
            />
          </div>

          <ul className="m-0 flex list-none flex-col gap-3 p-1 md:hidden">
            {zones.map((zone) => (
              <li
                key={zone.id}
                className="flex flex-col gap-3 rounded-tile border border-line p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <h3 className="font-bold text-ink">{zone.name}</h3>
                    <span className="text-xs text-faint tnum">{zone.code}</span>
                  </div>
                  <span className="shrink-0 text-right">
                    <span className="block text-lg font-extrabold tnum">
                      {zoneCodeCount(zone) ?? "—"}
                    </span>
                    <span className="block text-xs text-faint">postal codes</span>
                  </span>
                </div>
                <ModeCell zone={zone} />
                <MoneyCell zone={zone} currency={store.currency} />
                <Actions zone={zone} actions={actions} grow />
              </li>
            ))}
          </ul>
        </>
      )}

      <Dialog
        open={confirming !== null}
        onClose={() => {
          setConfirming(null);
          setFailure(null);
        }}
        title={confirming ? `Delete ${confirming.name}?` : "Delete zone"}
        description="Buyers in its postal codes go back to being told the store doesn't deliver there."
        size="sm"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setConfirming(null);
                setFailure(null);
              }}
              disabled={deleting}
            >
              Keep it
            </Button>
            <Button
              variant="danger"
              onClick={() => confirming && void runDelete(confirming)}
              disabled={deleting}
            >
              {deleting ? "Deleting…" : "Delete zone"}
            </Button>
          </>
        }
      >
        {confirming !== null && (zoneCodeCount(confirming) ?? 0) > 0 ? (
          <InlineAlert tone="warning" title="This zone still has postal codes">
            {zoneCodeCount(confirming)}{" "}
            {zoneCodeCount(confirming) === 1 ? "code is" : "codes are"} mapped to it. The server
            refuses to delete a zone that still holds codes — move them to another zone first.
          </InlineAlert>
        ) : null}
        {failure !== null ? (
          <InlineAlert tone="danger" title="That zone wasn't deleted" className="mt-3">
            {failure}
          </InlineAlert>
        ) : null}
      </Dialog>
    </Card>
  );
}
