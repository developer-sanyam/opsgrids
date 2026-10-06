import { useState } from "react";
import type { ReactNode } from "react";

import { listLedger, listLots, listRolls, listStock } from "@/api/tracking";
import type { InventoryItemOut } from "@/api/types";
import { Can, useCan } from "@opsgrids/client";
import { LotFormDialog } from "@/forms/LotFormDialog";
import { RollFormDialog } from "@/forms/RollFormDialog";
import { StockStatusChangeDialog } from "@/forms/StockStatusChangeDialog";
import { StockTransferDialog } from "@/forms/StockTransferDialog";
import { FindStockPanel } from "@/locate/FindStockPanel";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { Button, Card, EmptyState, SectionHeader } from "@/ui";
import { PackageIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { LedgerTable } from "./LedgerTable";
import { LotsTable } from "./LotsTable";
import { NoAccess } from "./pageParts";
import { RollsTable } from "./RollsTable";
import { StockTable } from "./StockTable";
import { useCursorList } from "./useCursorList";

interface PanelProps {
  item: InventoryItemOut;
  /** Called after a write on a panel changes a figure the item header shows. */
  onChanged: () => void;
}

/** Wraps one tab's content: the same grant that gates the screens' reads gates these reads. */
function StockReadGate({ what, children }: { what: string; children: ReactNode }) {
  const canRead = useCan("operations.inventory.stock.read", "ORG");
  return canRead ? <>{children}</> : <NoAccess what={what} />;
}

/** The item's stock by location: every bucket, with transfer and status change on each. */
export function ItemLocationsPanel({ item, onChanged }: PanelProps) {
  return (
    <StockReadGate what="this item's stock">
      <LocationsList item={item} onChanged={onChanged} />
    </StockReadGate>
  );
}

function LocationsList({ item, onChanged }: PanelProps) {
  const toast = useToast();
  const list = useCursorList(
    (cursor, signal) => listStock({ item_id: item.id, cursor, limit: 50, signal }),
    [item.id],
  );
  const [transferring, setTransferring] = useState<OnSiteRow | null>(null);
  const [changing, setChanging] = useState<OnSiteRow | null>(null);
  const done = (message: string) => {
    toast.success(message);
    list.reload();
    onChanged();
  };
  return (
    <div className="flex flex-col gap-6">
      <FindStockPanel itemId={item.id} subject={item.sku} uom={item.uom} />
      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="Locations" subtitle="Where this item's stock sits, by status, lot and roll." />
        </div>
        <StockTable list={list} hideItem onTransfer={setTransferring} onChangeStatus={setChanging} />
        {transferring ? (
          <StockTransferDialog
            row={transferring}
            onClose={() => setTransferring(null)}
            onSaved={() => {
              setTransferring(null);
              done("Stock transferred.");
            }}
          />
        ) : null}
        {changing ? (
          <StockStatusChangeDialog
            row={changing}
            onClose={() => setChanging(null)}
            onSaved={() => {
              setChanging(null);
              done("Status changed.");
            }}
          />
        ) : null}
      </Card>
    </div>
  );
}

/** The item's lots, and registering a new one when the item is lot-tracked. */
export function ItemLotsPanel({ item }: PanelProps) {
  return (
    <StockReadGate what="this item's lots">
      <LotsList item={item} />
    </StockReadGate>
  );
}

function LotsList({ item }: { item: InventoryItemOut }) {
  // Checked before any request is made: an untracked item can have no lots to ask for.
  if (!item.track_lot) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<PackageIcon className="size-6" />}
          title="This item isn't tracked by lot"
          description="Turn on lot tracking in Edit to register lots and post stock into them."
        />
      </Card>
    );
  }
  return <TrackedLots item={item} />;
}

function TrackedLots({ item }: { item: InventoryItemOut }) {
  const toast = useToast();
  const canWrite = useCan("operations.inventory.lot.write", "ORG");
  const list = useCursorList((cursor, signal) => listLots({ item_id: item.id, cursor, limit: 50, signal }), [item.id]);
  const [creating, setCreating] = useState(false);
  return (
    <Card className="p-2 sm:p-4">
      <div className="flex flex-col gap-3 px-3 pt-2 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <SectionHeader title="Lots" subtitle="Batches of this item." />
        <Can permission="operations.inventory.lot.write" scope="ORG">
          <Button size="sm" variant="outline" leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
            Create lot
          </Button>
        </Can>
      </div>
      <LotsTable list={list} hideItem {...(canWrite ? { onCreate: () => setCreating(true) } : {})} />
      {creating ? (
        <LotFormDialog
          item={item}
          onClose={() => setCreating(false)}
          onSaved={(lot) => {
            setCreating(false);
            toast.success(`Lot ${lot.lot_no} created.`);
            list.reload();
          }}
        />
      ) : null}
    </Card>
  );
}

/** The item's rolls, and registering a new one when the item is roll-tracked. */
export function ItemRollsPanel({ item }: PanelProps) {
  return (
    <StockReadGate what="this item's rolls">
      <RollsList item={item} />
    </StockReadGate>
  );
}

function RollsList({ item }: { item: InventoryItemOut }) {
  if (!item.track_roll) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<PackageIcon className="size-6" />}
          title="This item isn't tracked by roll"
          description="Turn on roll tracking in Edit to register rolls and post stock into them."
        />
      </Card>
    );
  }
  return <TrackedRolls item={item} />;
}

function TrackedRolls({ item }: { item: InventoryItemOut }) {
  const toast = useToast();
  const canWrite = useCan("operations.inventory.roll.write", "ORG");
  const list = useCursorList((cursor, signal) => listRolls({ item_id: item.id, cursor, limit: 50, signal }), [item.id]);
  const [creating, setCreating] = useState(false);
  return (
    <Card className="p-2 sm:p-4">
      <div className="flex flex-col gap-3 px-3 pt-2 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <SectionHeader title="Rolls" subtitle="Individual rolls of this item." />
        <Can permission="operations.inventory.roll.write" scope="ORG">
          <Button size="sm" variant="outline" leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
            Create roll
          </Button>
        </Can>
      </div>
      <RollsTable list={list} hideItem {...(canWrite ? { onCreate: () => setCreating(true) } : {})} />
      {creating ? (
        <RollFormDialog
          item={item}
          onClose={() => setCreating(false)}
          onSaved={(roll) => {
            setCreating(false);
            toast.success(`Roll ${roll.roll_no} created.`);
            list.reload();
          }}
        />
      ) : null}
    </Card>
  );
}

/** The item's slice of the ledger, newest first. */
export function ItemLedgerPanel({ item }: PanelProps) {
  return (
    <StockReadGate what="this item's ledger">
      <LedgerList item={item} />
    </StockReadGate>
  );
}

function LedgerList({ item }: { item: InventoryItemOut }) {
  const list = useCursorList((cursor, signal) => listLedger({ item_id: item.id, cursor, limit: 50, signal }), [item.id]);
  return (
    <Card className="p-2 sm:p-4">
      <div className="px-3 pt-2 pb-3">
        <SectionHeader title="Ledger" subtitle="Every change to this item's stock, newest first. Append-only." />
      </div>
      <LedgerTable list={list} hideItem />
    </Card>
  );
}
