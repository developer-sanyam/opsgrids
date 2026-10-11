import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  approveProductionOrder,
  getBomVersion,
  getProductionOrder,
  startProductionOrder,
} from "@/api/client";
import type {
  BomSubstituteOut,
  ProductionActionOut,
  ProductionMaterialOut,
  ProductionOrderDetailOut,
  ShortageOut,
} from "@/api/types";
import { useLocationOptions } from "@/data/useLocationOptions";
import { CancelOrderDialog } from "@/forms/production/CancelOrderDialog";
import { CompleteDialog } from "@/forms/production/CompleteDialog";
import { IssueDialog } from "@/forms/production/IssueDialog";
import { OutputDialog } from "@/forms/production/OutputDialog";
import { ReserveDialog } from "@/forms/production/ReserveDialog";
import { ReturnDialog } from "@/forms/production/ReturnDialog";
import { SubstituteMaterialDialog } from "@/forms/production/SubstituteMaterialDialog";
import { WastageDialog } from "@/forms/production/WastageDialog";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { ProductionOrderFormDialog } from "@/forms/ProductionOrderFormDialog";
import { BackLink } from "@/routes/manufacturing/BackLink";
import {
  MaterialsSection,
  OutputsSection,
  PicksSection,
  VarianceSection,
  WastageSection,
} from "@/routes/manufacturing/ProductionOrderSections";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { availableActions, PRODUCTION_PERMISSIONS } from "@/lib/productionOrderActions";
import type { ProductionAction } from "@/lib/productionOrderActions";
import { problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  Button,
  Card,
  ErrorState,
  InlineAlert,
  PageHeader,
  ProductionOrderStatusPill,
  Skeleton,
} from "@/ui";
import { useToast } from "@opsgrids/ui";

type Dialog = Exclude<ProductionAction, "approve" | "start"> | null;

const DONE_MESSAGE: Record<ProductionAction, string> = {
  approve: "Order approved.",
  start: "Production started.",
  reserve: "Material reserved.",
  issue: "Material issued to work in progress.",
  output: "Output recorded.",
  wastage: "Wastage recorded.",
  return: "Material returned to stock.",
  complete: "Order completed.",
  cancel: "Order cancelled.",
  edit: "Order updated.",
};

function ShortageBanner({ shortages }: { shortages: ShortageOut[] }) {
  return (
    <InlineAlert tone="warning" title="Material shortage">
      <p>Not everything could be reserved from available stock.</p>
      <ul className="mt-2 flex flex-col gap-1">
        {shortages.map((s) => (
          <li key={s.material_id} className="tnum text-sm">
            <span className="font-semibold">{s.sku}</span>: short {formatQty(s.short)} (needs {formatQty(s.required)}, covered{" "}
            {formatQty(s.covered)})
          </li>
        ))}
      </ul>
    </InlineAlert>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="text-sm text-ink">{children}</dd>
    </div>
  );
}

/**
 * One production order: its status, pinned BOM version, planned against produced, an action bar
 * driven by the status and the caller's permissions, and the materials, picks, outputs, wastage and
 * variance. Every action answers with the updated order, which replaces local state — nothing is
 * refetched. The bar is presentation: the server re-checks the status and permission on each call.
 */
export function ProductionOrderDetailPage() {
  const { orderId = "" } = useParams();
  const toast = useToast();
  const grants = {
    write: useCan(PRODUCTION_PERMISSIONS.write, "ORG"),
    approve: useCan(PRODUCTION_PERMISSIONS.approve, "ORG"),
    issue: useCan(PRODUCTION_PERMISSIONS.issue, "ORG"),
    complete: useCan(PRODUCTION_PERMISSIONS.complete, "ORG"),
  };
  const locations = useLocationOptions();

  const state = useAsync(() => getProductionOrder(orderId), [orderId]);
  const [order, setOrder] = useState<ProductionOrderDetailOut | null>(null);
  // `state` is a fresh object every render; its `data` is stable per load, so only a real
  // (re)load replaces what an action's response has since put in local state.
  useEffect(() => {
    if (state.data) setOrder(state.data);
  }, [state.data]);

  const [shortages, setShortages] = useState<ShortageOut[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [substituteFor, setSubstituteFor] = useState<ProductionMaterialOut | null>(null);

  const canSubstituteNow = grants.write && order !== null && (order.status === "DRAFT" || order.status === "APPROVED");
  const versionState = useAsync(
    () =>
      canSubstituteNow && order !== null
        ? getBomVersion(order.bom_id, order.bom_version_id)
        : Promise.resolve(null),
    [canSubstituteNow, order?.bom_id, order?.bom_version_id],
  );
  const substitutesByComponent = useMemo(() => {
    const map = new Map<string, BomSubstituteOut[]>();
    if (versionState.status === "success" && versionState.data) {
      for (const c of versionState.data.components) map.set(c.component_item_id, c.substitutes);
    }
    return map;
  }, [versionState.status, versionState.data]);

  function applied(action: ProductionAction) {
    return (result: ProductionActionOut) => {
      setOrder(result);
      setShortages(action === "reserve" ? result.shortages : []);
      setDialog(null);
      toast.success(DONE_MESSAGE[action]);
    };
  }

  const approve = useOrderAction<void>((_input, options) => approveProductionOrder(orderId, options), applied("approve"));
  const start = useOrderAction<void>((_input, options) => startProductionOrder(orderId, options), applied("start"));

  const back = <BackLink to="/manufacturing/orders">Back to production orders</BackLink>;

  if (state.status === "loading" || (state.status === "success" && order === null)) {
    return (
      <div className="rise flex flex-col gap-6" aria-busy="true" aria-label="Loading production order">
        {back}
        <Skeleton className="h-24 w-full rounded-tile" />
        <Skeleton className="h-40 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        {back}
        <ErrorState title="Couldn't load this production order" message={problemMessage(state.error)} onRetry={state.reload} />
      </div>
    );
  }
  if (order === null) return null;

  const actions = availableActions(order, grants);
  const has = (a: ProductionAction) => actions.includes(a);
  const actionError = approve.error ?? start.error;

  return (
    <div className="rise flex flex-col gap-6">
      {back}
      <PageHeader
        eyebrow="Production order"
        title={order.number}
        description={`${order.sku} — ${order.name}`}
        actions={
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Order actions">
            <ProductionOrderStatusPill status={order.status} />
            {has("approve") ? (
              <Button loading={approve.pending} onClick={() => approve.mutate(undefined)}>
                Approve
              </Button>
            ) : null}
            {has("start") ? (
              <Button loading={start.pending} onClick={() => start.mutate(undefined)}>
                Start production
              </Button>
            ) : null}
            {has("reserve") ? (
              <Button variant="secondary" onClick={() => setDialog("reserve")}>
                Reserve material
              </Button>
            ) : null}
            {has("issue") ? (
              <Button variant="secondary" onClick={() => setDialog("issue")}>
                Issue material
              </Button>
            ) : null}
            {has("output") ? (
              <Button variant="secondary" onClick={() => setDialog("output")}>
                Record output
              </Button>
            ) : null}
            {has("wastage") ? (
              <Button variant="secondary" onClick={() => setDialog("wastage")}>
                Record wastage
              </Button>
            ) : null}
            {has("return") ? (
              <Button variant="secondary" onClick={() => setDialog("return")}>
                Return material
              </Button>
            ) : null}
            {has("complete") ? <Button onClick={() => setDialog("complete")}>Complete order</Button> : null}
            {has("edit") ? (
              <Button variant="secondary" onClick={() => setDialog("edit")}>
                Edit
              </Button>
            ) : null}
            {has("cancel") ? (
              <Button variant="destructive" onClick={() => setDialog("cancel")}>
                Cancel order
              </Button>
            ) : null}
          </div>
        }
      />

      {actionError ? <InlineAlert tone="danger" title={problemMessage(actionError)} /> : null}
      {shortages.length > 0 ? <ShortageBanner shortages={shortages} /> : null}
      {order.status === "CANCELLED" ? (
        <InlineAlert tone="info" title="This order was cancelled" live="off">
          {order.cancel_reason || "No reason was given."}
        </InlineAlert>
      ) : null}

      <Card className="p-4 sm:p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Planned">
            <span className="tnum">
              {formatQty(order.planned_quantity)} {order.uom}
            </span>
          </Fact>
          <Fact label="Produced">
            <span className="tnum">
              {formatQty(order.produced_quantity)} of {formatQty(order.planned_quantity)} {order.uom}
            </span>
          </Fact>
          <Fact label="BOM version">
            <Link
              to={`/manufacturing/boms/${order.bom_id}/versions/${order.bom_version_id}`}
              className="font-semibold hover:underline"
            >
              {order.bom_code} · version {order.bom_version_no}
            </Link>
          </Fact>
          <Fact label="Due">{formatDate(order.due_date)}</Fact>
          <Fact label="Planned start">{formatDate(order.planned_start)}</Fact>
          <Fact label="Output location">{locations.label(order.output_location_id)}</Fact>
          <Fact label="WIP location">
            {order.wip_location_id ? locations.label(order.wip_location_id) : "Default"}
          </Fact>
          <Fact label="Notes">{order.notes || "—"}</Fact>
        </dl>
      </Card>

      <MaterialsSection
        materials={order.materials}
        onUseSubstitute={canSubstituteNow ? setSubstituteFor : null}
        canSubstitute={(m) => (substitutesByComponent.get(m.component_item_id)?.length ?? 0) > 0}
      />
      <PicksSection order={order} locationLabel={locations.label} />
      <OutputsSection order={order} locationLabel={locations.label} />
      <WastageSection order={order} locationLabel={locations.label} />
      <VarianceSection order={order} />

      {dialog === "reserve" ? <ReserveDialog order={order} onClose={() => setDialog(null)} onDone={applied("reserve")} /> : null}
      {dialog === "issue" ? <IssueDialog order={order} onClose={() => setDialog(null)} onDone={applied("issue")} /> : null}
      {dialog === "output" ? <OutputDialog order={order} onClose={() => setDialog(null)} onDone={applied("output")} /> : null}
      {dialog === "wastage" ? <WastageDialog order={order} onClose={() => setDialog(null)} onDone={applied("wastage")} /> : null}
      {dialog === "return" ? <ReturnDialog order={order} onClose={() => setDialog(null)} onDone={applied("return")} /> : null}
      {dialog === "complete" ? <CompleteDialog order={order} onClose={() => setDialog(null)} onDone={applied("complete")} /> : null}
      {dialog === "cancel" ? <CancelOrderDialog order={order} onClose={() => setDialog(null)} onDone={applied("cancel")} /> : null}
      {dialog === "edit" ? (
        <ProductionOrderFormDialog
          order={order}
          onClose={() => setDialog(null)}
          onSaved={(saved) => {
            setOrder(saved);
            setDialog(null);
            toast.success(DONE_MESSAGE.edit);
          }}
        />
      ) : null}
      {substituteFor ? (
        <SubstituteMaterialDialog
          orderId={order.id}
          material={substituteFor}
          substitutes={substitutesByComponent.get(substituteFor.component_item_id) ?? []}
          onClose={() => setSubstituteFor(null)}
          onDone={(saved) => {
            setOrder(saved);
            setSubstituteFor(null);
            toast.success("Material updated.");
          }}
        />
      ) : null}
    </div>
  );
}
