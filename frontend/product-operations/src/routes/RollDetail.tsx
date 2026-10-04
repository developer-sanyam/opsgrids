import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getRollTrace, listRollEvents, listStock } from "@/api/tracking";
import type { RollEventOut } from "@/api/trackingTypes";
import type { OnSiteRow } from "@/lib/rollBuckets";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import { RollSplitDialog } from "@/forms/RollSplitDialog";
import { StockStatusChangeDialog } from "@/forms/StockStatusChangeDialog";
import { StockTransferDialog } from "@/forms/StockTransferDialog";
import { formatDateTime } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { rollEventLabel } from "@/lib/trackingLabels";
import { Button, Card, EmptyState, ErrorState, PageHeader, SectionHeader, Skeleton } from "@/ui";
import type { Column } from "@/ui";
import { LayersIcon, useToast } from "@opsgrids/ui";
import { RollStateBadge } from "@/tracking/badges";
import { CursorTable } from "@/tracking/CursorTable";
import { BackLink, MetricGrid, RequireStockRead } from "@/tracking/pageParts";
import { StockTable } from "@/tracking/StockTable";
import { TraceList } from "@/tracking/TraceList";
import { useCursorList } from "@/tracking/useCursorList";

/**
 * One roll: its figures, its lineage (the rolls it was split or merged from, and the rolls that
 * came of it, plus its lot), its history, and where its stock sits. Splitting needs
 * `operations.inventory.roll.write` and a roll still active — a split or merged roll has already
 * become something else and can't be split again.
 */
export function RollDetailPage() {
  return (
    <RequireStockRead what="this roll">
      <RollDetail />
    </RequireStockRead>
  );
}

function RollDetail() {
  const { id = "" } = useParams();
  const toast = useToast();
  const trace = useAsync(() => getRollTrace(id), [id]);
  const [splitting, setSplitting] = useState(false);
  const [transferring, setTransferring] = useState<OnSiteRow | null>(null);
  const [changing, setChanging] = useState<OnSiteRow | null>(null);

  const events = useCursorList((cursor, signal) => listRollEvents(id, { cursor, limit: 50, signal }), [id]);
  const stock = useCursorList((cursor, signal) => listStock({ roll_id: id, cursor, limit: 50, signal }), [id]);

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
        <BackLink to="/inventory/rolls">Back to rolls</BackLink>
        <Card className="p-6">
          <ErrorState
            title="Couldn't load this roll"
            message={problemMessage(trace.error)}
            onRetry={trace.reload}
          />
        </Card>
      </div>
    );
  }

  const { roll, lot, ancestors, descendants, lot_ancestors: lotAncestors } = trace.data;

  const eventColumns: Column<RollEventOut>[] = [
    {
      key: "when",
      header: "When",
      render: (e) => <span className="tnum whitespace-nowrap text-muted">{formatDateTime(e.occurred_at)}</span>,
    },
    { key: "event", header: "Event", render: (e) => <span className="font-semibold text-ink">{rollEventLabel(e.kind)}</span> },
    {
      key: "quantity",
      header: "Quantity",
      align: "right",
      render: (e) =>
        e.quantity ? (
          <span className="tnum text-ink-soft">
            {formatQty(e.quantity)} {roll.uom}
          </span>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "related",
      header: "Related roll",
      render: (e) =>
        e.related_roll_id ? (
          <Link
            to={`/inventory/rolls/${e.related_roll_id}`}
            className="font-semibold text-accent hover:text-accent-strong transition-colors"
          >
            Open roll
          </Link>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink to="/inventory/rolls">Back to rolls</BackLink>

      <PageHeader
        eyebrow={`Roll · ${roll.sku}${roll.lot_no ? ` · lot ${roll.lot_no}` : ""}`}
        title={roll.roll_no}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RollStateBadge state={roll.state} />
            {roll.state === "ACTIVE" ? (
              <Can permission="operations.inventory.roll.write" scope="ORG">
                <Button onClick={() => setSplitting(true)}>Split roll</Button>
              </Can>
            ) : null}
          </div>
        }
      />

      <MetricGrid
        label="Roll figures"
        items={[
          { label: "On hand", value: `${formatQty(roll.on_hand_qty)} ${roll.uom}` },
          {
            label: "Measured weight",
            value: roll.secondary_qty && roll.secondary_uom ? `${formatQty(roll.secondary_qty)} ${roll.secondary_uom}` : "—",
          },
          { label: "Width", value: roll.width_mm ? `${roll.width_mm} mm` : "—" },
          { label: "GSM", value: roll.gsm ?? "—" },
          { label: "Shade", value: roll.shade ?? "—" },
          { label: "Grade", value: roll.grade ?? "—" },
          { label: "Lot", value: lot ? <Link to={`/inventory/lots/${lot.id}`} className="text-accent hover:text-accent-strong transition-colors">{lot.lot_no}</Link> : "—" },
          { label: "Registered length", value: `${formatQty(roll.primary_qty)} ${roll.uom}` },
        ]}
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <SectionHeader title="Parent and children" subtitle="The rolls this one came from, and the rolls made of it." />
        <div className="grid gap-6 md:grid-cols-2">
          <TraceList
            title="Came from"
            nodes={ancestors}
            kind="roll"
            emptyText="This roll wasn't made by a split or merge."
          />
          <TraceList
            title="Became"
            nodes={descendants}
            kind="roll"
            emptyText="This roll hasn't been split or merged."
          />
        </div>
        {lotAncestors.length > 0 ? (
          <TraceList title="Its lot came from" nodes={lotAncestors} kind="lot" emptyText="" />
        ) : null}
        <p className="text-sm text-muted">
          <Link
            to={`/inventory/ledger?item=${roll.inventory_item_id}&roll=${roll.id}`}
            className="font-semibold text-accent hover:text-accent-strong transition-colors"
          >
            View this roll&apos;s ledger
          </Link>
        </p>
      </Card>

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="History" subtitle="What has happened to this roll, newest first." />
        </div>
        <CursorTable
          list={events}
          columns={eventColumns}
          rowKey={(e) => e.id}
          caption={`History of roll ${roll.roll_no}`}
          noun="events"
          errorTitle="Couldn't load this roll's history"
          emptyState={
            <EmptyState
              icon={<LayersIcon className="size-6" />}
              title="No history yet"
              description="Splits, merges, moves and re-measurements appear here."
            />
          }
        />
      </Card>

      <Card className="p-2 sm:p-4">
        <div className="px-3 pt-2 pb-3">
          <SectionHeader title="Stock" subtitle="Where this roll's stock sits." />
        </div>
        <StockTable list={stock} hideItem onTransfer={setTransferring} onChangeStatus={setChanging} />
      </Card>

      {splitting ? (
        <RollSplitDialog
          roll={roll}
          onClose={() => setSplitting(false)}
          onSaved={(result) => {
            setSplitting(false);
            toast.success(`Split into ${result.children.length} rolls.`);
            trace.reload();
            events.reload();
            stock.reload();
          }}
        />
      ) : null}
      {transferring ? (
        <StockTransferDialog
          row={transferring}
          onClose={() => setTransferring(null)}
          onSaved={() => {
            setTransferring(null);
            toast.success("Stock transferred.");
            stock.reload();
            events.reload();
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
            events.reload();
            trace.reload();
          }}
        />
      ) : null}
    </div>
  );
}
