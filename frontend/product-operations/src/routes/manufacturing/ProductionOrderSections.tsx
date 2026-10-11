import type {
  ProductionMaterialOut,
  ProductionOrderDetailOut,
  ProductionOutputOut,
  ProductionPickOut,
  ProductionWastageOut,
  VarianceLineOut,
  WastageType,
} from "@/api/types";
import { WASTAGE_TYPE_LABEL } from "@/data/wastageTypes";
import { formatDateTime } from "@/lib/format";
import { formatPercent, formatQty, qtyToMilli } from "@/lib/quantity";
import { Card, DataTable, EmptyState, RowActions, SectionHeader, Tag } from "@/ui";
import type { Column } from "@/ui";

function Amount({ value, uom }: { value: string; uom?: string }) {
  return (
    <span className="tnum">
      {formatQty(value)}
      {uom ? ` ${uom}` : ""}
    </span>
  );
}

const shortId = (id: string | null) => (id ? id.slice(0, 8) : "—");

/* --------------------------------- materials -------------------------------- */

interface MaterialsProps {
  materials: ProductionMaterialOut[];
  /** Offers "Use substitute" on a line, or `null` when this user/order/line cannot. */
  onUseSubstitute: ((material: ProductionMaterialOut) => void) | null;
  canSubstitute: (material: ProductionMaterialOut) => boolean;
}

export function MaterialsSection({ materials, onUseSubstitute, canSubstitute }: MaterialsProps) {
  const columns: Column<ProductionMaterialOut>[] = [
    {
      key: "sku",
      header: "Material",
      render: (m) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{m.sku}</span>
          <span className="text-xs text-muted">{m.name}</span>
          {m.is_substitute ? (
            <span className="mt-1">
              <Tag tone="warning">Substitute for {m.component_sku}</Tag>
            </span>
          ) : null}
        </div>
      ),
    },
    { key: "required", header: "Required", align: "right", render: (m) => <Amount value={m.required} uom={m.uom} /> },
    { key: "reserved", header: "Reserved", align: "right", render: (m) => <Amount value={m.reserved} /> },
    { key: "issued", header: "Issued", align: "right", render: (m) => <Amount value={m.issued} /> },
    { key: "consumed", header: "Consumed", align: "right", render: (m) => <Amount value={m.consumed} /> },
    { key: "returned", header: "Returned", align: "right", render: (m) => <Amount value={m.returned} /> },
    { key: "wasted", header: "Wasted", align: "right", render: (m) => <Amount value={m.wasted} /> },
    { key: "wip", header: "WIP", align: "right", render: (m) => <Amount value={m.wip} /> },
    {
      key: "open",
      header: "Open need",
      align: "right",
      render: (m) => (
        <span className={qtyToMilli(m.open_need) > 0 ? "tnum font-semibold text-ink" : "tnum text-muted"}>
          {formatQty(m.open_need)}
        </span>
      ),
    },
    {
      key: "scrap",
      header: "Scrap",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (m) => <span className="tnum text-muted">{formatPercent(m.scrap_percent)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (m) =>
        onUseSubstitute && canSubstitute(m) ? (
          <RowActions
            label={`Material ${m.sku}`}
            actions={[{ label: "Use substitute", onSelect: () => onUseSubstitute(m) }]}
          />
        ) : null,
    },
  ];

  return (
    <Card className="flex flex-col gap-2 p-2 sm:p-4">
      <div className="px-2 pt-2 sm:px-0 sm:pt-0">
        <SectionHeader title="Materials" subtitle="What the BOM version pinned to this order needs." />
      </div>
      <DataTable
        columns={columns}
        rows={materials}
        rowKey={(m) => m.id}
        caption="Materials"
        emptyState={<EmptyState title="No materials" description="The pinned BOM version has no components." />}
      />
    </Card>
  );
}

/* ----------------------------------- picks ---------------------------------- */

interface PicksProps {
  order: ProductionOrderDetailOut;
  locationLabel: (id: string) => string;
}

export function PicksSection({ order, locationLabel }: PicksProps) {
  const sku = (materialId: string) => order.materials.find((m) => m.id === materialId)?.sku ?? shortId(materialId);
  const columns: Column<ProductionPickOut>[] = [
    { key: "material", header: "Material", render: (p) => <span className="font-semibold text-ink">{sku(p.material_id)}</span> },
    { key: "location", header: "Location", render: (p) => <span className="text-muted">{locationLabel(p.source_location_id)}</span> },
    { key: "lot", header: "Lot", cellClassName: "hidden md:table-cell", headerClassName: "hidden md:table-cell", render: (p) => <span className="tnum text-muted">{shortId(p.lot_id)}</span> },
    { key: "roll", header: "Roll", cellClassName: "hidden md:table-cell", headerClassName: "hidden md:table-cell", render: (p) => <span className="tnum text-muted">{shortId(p.roll_id)}</span> },
    { key: "reserved", header: "Reserved", align: "right", render: (p) => <Amount value={p.reserved} /> },
    { key: "issued", header: "Issued", align: "right", render: (p) => <Amount value={p.issued} /> },
    { key: "wip", header: "In WIP", align: "right", render: (p) => <Amount value={p.wip} /> },
  ];
  return (
    <Card className="flex flex-col gap-2 p-2 sm:p-4">
      <div className="px-2 pt-2 sm:px-0 sm:pt-0">
        <SectionHeader title="Picks" subtitle="Where each material is reserved from and issued out of." />
      </div>
      <DataTable
        columns={columns}
        rows={order.picks}
        rowKey={(p) => p.id}
        caption="Picks"
        emptyState={<EmptyState title="Nothing picked yet" description="Reserve or issue material to see where it comes from." />}
      />
    </Card>
  );
}

/* ---------------------------------- outputs --------------------------------- */

interface OutputsProps {
  order: ProductionOrderDetailOut;
  locationLabel: (id: string) => string;
}

export function OutputsSection({ order, locationLabel }: OutputsProps) {
  const columns: Column<ProductionOutputOut>[] = [
    { key: "quantity", header: "Quantity", align: "right", render: (o) => <Amount value={o.quantity} uom={order.uom} /> },
    { key: "status", header: "Stock status", render: (o) => (o.status === "QUALITY_HOLD" ? <Tag tone="warning">Quality hold</Tag> : <Tag tone="positive">Available</Tag>) },
    { key: "location", header: "Location", render: (o) => <span className="text-muted">{locationLabel(o.location_id)}</span> },
    { key: "lot", header: "Lot", cellClassName: "hidden md:table-cell", headerClassName: "hidden md:table-cell", render: (o) => <span className="tnum text-muted">{shortId(o.lot_id)}</span> },
    { key: "note", header: "Note", cellClassName: "hidden md:table-cell", headerClassName: "hidden md:table-cell", render: (o) => <span className="text-muted">{o.note || "—"}</span> },
    { key: "when", header: "Recorded", render: (o) => <span className="text-muted">{formatDateTime(o.created_at)}</span> },
  ];
  return (
    <Card className="flex flex-col gap-2 p-2 sm:p-4">
      <div className="px-2 pt-2 sm:px-0 sm:pt-0">
        <SectionHeader title="Outputs" subtitle="Finished goods received into stock." />
      </div>
      <DataTable
        columns={columns}
        rows={order.outputs}
        rowKey={(o) => o.id}
        caption="Outputs"
        emptyState={<EmptyState title="No output yet" description="Recorded output appears here." />}
      />
    </Card>
  );
}

/* ---------------------------------- wastage --------------------------------- */

export function WastageSection({ order, locationLabel }: OutputsProps) {
  const groups = new Map<WastageType, ProductionWastageOut[]>();
  for (const w of order.wastage) groups.set(w.wastage_type, [...(groups.get(w.wastage_type) ?? []), w]);
  const sku = (materialId: string) => order.materials.find((m) => m.id === materialId);
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <SectionHeader title="Wastage by type" subtitle="Material written off from work in progress." />
      {groups.size === 0 ? (
        <EmptyState title="No wastage" description="Anything written off appears here, grouped by type." />
      ) : (
        [...groups.entries()].map(([type, rows]) => (
          <section key={type} aria-label={`${WASTAGE_TYPE_LABEL[type]} wastage`} className="flex flex-col gap-2">
            <h3 className="text-card-title">{WASTAGE_TYPE_LABEL[type]}</h3>
            <ul className="flex flex-col gap-1">
              {rows.map((w) => {
                const material = sku(w.material_id);
                return (
                  <li key={w.id} className="flex flex-wrap items-baseline gap-x-3 text-sm">
                    <span className="font-semibold text-ink">{material?.sku ?? shortId(w.material_id)}</span>
                    <Amount value={w.quantity} {...(material ? { uom: material.uom } : {})} />
                    <span className="text-muted">{locationLabel(w.location_id)}</span>
                    {w.note ? <span className="text-muted">{w.note}</span> : null}
                    <span className="text-muted">{formatDateTime(w.created_at)}</span>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
    </Card>
  );
}

/* ---------------------------------- variance -------------------------------- */

function varianceWord(variance: string): string {
  const milli = qtyToMilli(variance);
  if (milli > 0) return "Over-use";
  if (milli < 0) return "Under-use";
  return "On standard";
}

export function VarianceSection({ order }: { order: ProductionOrderDetailOut }) {
  const columns: Column<VarianceLineOut>[] = [
    {
      key: "sku",
      header: "Material",
      render: (v) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{v.sku}</span>
          <span className="text-xs text-muted">{v.name}</span>
        </div>
      ),
    },
    { key: "standard", header: "Standard", align: "right", render: (v) => <Amount value={v.standard} uom={v.uom} /> },
    { key: "actual", header: "Actual", align: "right", render: (v) => <Amount value={v.actual} uom={v.uom} /> },
    {
      key: "variance",
      header: "Variance",
      align: "right",
      render: (v) => (
        <div className="flex flex-col items-end leading-tight">
          <Amount value={v.variance} uom={v.uom} />
          <span className="text-xs text-muted">{varianceWord(v.variance)}</span>
        </div>
      ),
    },
    {
      key: "percent",
      header: "Variance %",
      align: "right",
      render: (v) => <span className="tnum">{v.variance_percent === null ? "—" : formatPercent(v.variance_percent)}</span>,
    },
  ];
  return (
    <Card className="flex flex-col gap-2 p-2 sm:p-4">
      <div className="px-2 pt-2 sm:px-0 sm:pt-0">
        <SectionHeader
          title="Variance"
          subtitle={`Standard against actual for the ${formatQty(order.variance.produced_quantity)} ${order.uom} produced so far. Positive is over-use.`}
        />
      </div>
      <DataTable
        columns={columns}
        rows={order.variance.lines}
        rowKey={(v) => v.material_id}
        caption="Variance"
        emptyState={<EmptyState title="Nothing to compare yet" description="Variance appears once output is recorded." />}
      />
    </Card>
  );
}
