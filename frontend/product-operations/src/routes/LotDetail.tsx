import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getLotTrace, listRolls, listStock } from "@/api/tracking";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { problemMessage, useAsync } from "@opsgrids/client";
import { StockStatusChangeDialog } from "@/forms/StockStatusChangeDialog";
import { StockTransferDialog } from "@/forms/StockTransferDialog";
import { FindStockPanel } from "@/locate/FindStockPanel";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { Card, ErrorState, PageHeader, SectionHeader, Skeleton } from "@/ui";
import { useToast } from "@opsgrids/ui";
import { LotQualityBadge } from "@/tracking/badges";
import { BackLink, MetricGrid, RequireStockRead } from "@/tracking/pageParts";
import { RollsTable } from "@/tracking/RollsTable";
import { StockTable } from "@/tracking/StockTable";
import { TraceList } from "@/tracking/TraceList";
import { useCursorList } from "@/tracking/useCursorList";

/**
 * One lot: its figures, its genealogy (every lot it came from and everything that came of it),
 * where its stock sits, and the rolls cut from it. The trace is one response from
 * `GET …/lots/{id}/trace`; the stock and the rolls are cursor lists of their own, so a lot with
 * hundreds of rolls does not hold the page.
 */
export function LotDetailPage() {
  return (
    <RequireStockRead what="this lot">
      <LotDetail />
    </RequireStockRead>
  );
}

function LotDetail() {
  const { id = "" } = useParams();
  const toast = useToast();
  const trace = useAsync(() => getLotTrace(id), [id]);
  const [transferring, setTransferring] = useState<OnSiteRow | null>(null);
  const [changing, setChanging] = useState<OnSiteRow | null>(null);

  const stock = useCursorList((cursor, signal) => listStock({ lot_id: id, cursor, limit: 50, signal }), [id]);
  const rolls = useCursorList((cursor, signal) => listRolls({ lot_id: id, cursor, limit: 50, signal }), [id]);

  if (trace.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-24 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (trace.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink to="/inventory/lots">Back to lots</BackLink>
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this lot"
            message={problemMessage(trace.error)}
            onRetry={trace.reload}
          />
        </Card>
      </div>
    );
  }

  const { lot, ancestors, descendants } = trace.data;
  return (
    <div className="rise flex flex-col gap-6">
      <BackLink to="/inventory/lots">Back to lots</BackLink>

      <PageHeader eyebrow={`Lot · ${lot.sku}`} title={lot.lot_no} actions={<LotQualityBadge status={lot.quality_status} />} />

      <MetricGrid
        label="Lot figures"
        items={[
          { label: "On hand", value: `${formatQty(lot.on_hand_qty)} ${lot.uom}` },
          { label: "Supplier lot", value: lot.supplier_lot_no ?? "—" },
          { label: "Received", value: formatDate(lot.received_on) },
          { label: "Expires", value: formatDate(lot.expires_on) },
        ]}
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <SectionHeader
          title="Genealogy"
          subtitle="Where this lot came from, and what was made of it."
        />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <TraceList
            title="Came from"
            nodes={ancestors}
            kind="lot"
            emptyText="This lot has no recorded parent lots."
          />
          <TraceList
            title="Went into"
            nodes={descendants}
            kind="lot"
            emptyText="Nothing has been made from this lot."
          />
        </div>
        <p className="text-sm text-muted">
          <Link
            to={`/inventory/ledger?item=${lot.inventory_item_id}&lot=${lot.id}`}
            className="font-semibold text-accent hover:text-accent-strong transition-colors"
          >
            View this lot&apos;s ledger
          </Link>
        </p>
      </Card>

      <FindStockPanel itemId={lot.inventory_item_id} lotId={lot.id} subject={`lot ${lot.lot_no}`} uom={lot.uom} />

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="Stock" subtitle="Where this lot's stock sits." />
        </div>
        <StockTable
          list={stock}
          hideItem
          onTransfer={setTransferring}
          onChangeStatus={setChanging}
        />
      </Card>

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="Rolls" subtitle="Rolls cut from this lot." />
        </div>
        <RollsTable list={rolls} hideItem />
      </Card>

      {transferring ? (
        <StockTransferDialog
          row={transferring}
          onClose={() => setTransferring(null)}
          onSaved={() => {
            setTransferring(null);
            toast.success("Stock transferred.");
            stock.reload();
            trace.reload();
          }}
        />
      ) : null}
      {changing ? (
        <StockStatusChangeDialog
          row={changing}
          onClose={() => setChanging(null)}
          onSaved={() => {
            setChanging(null);
            toast.success("Status changed.");
            stock.reload();
            trace.reload();
          }}
        />
      ) : null}
    </div>
  );
}
