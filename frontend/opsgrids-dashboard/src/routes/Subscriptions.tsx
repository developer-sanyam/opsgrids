import { useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  RowActions,
  Skeleton,
  StatusBadge,
  TextArea,
  statusTone,
  useToast,
} from "@opsgrids/ui";
import type { Column, StatusTone, TableSelection } from "@opsgrids/ui";

import { cancelSubscription, listProducts, listSubscriptionPlanSummaries, listSubscriptions } from "@/api";
import type { Product, Subscription, SubscriptionPlanSummary } from "@/api/types";
import { bundlesIncluding, joinNames, productNames } from "@/lib/bundles";
import { downloadCsv } from "@/lib/csv";
import { formatDate } from "@/lib/money";
import { PageHeader } from "@/ui";
import { shortProductName } from "../lib/productName";

import {
  canChangePlan,
  cancelBody,
  changePlanUnavailableReason,
  pendingLine,
  planName,
  priceLine,
  statusLine,
} from "@/lib/subscriptionCopy";
import { ChangePlanDialog } from "./subscriptions/ChangePlanDialog";
import { KeepPlanDialog } from "./subscriptions/KeepPlanDialog";

const WRITE_PERMISSION = "core.product.activation.write";

/**
 * `trial` and `cancelled` mean something different here than the shared registry's general sense
 * of those words (see `opsgrids-ui/src/lib/status.ts`) — this app has always shown a trial as
 * informational rather than at-risk, and a cancelled subscription as a finished fact rather than a
 * failure — so they take an explicit tone rather than the registry's. Listed in the sprint report.
 */
const STATE_TONE_OVERRIDE: Record<string, StatusTone> = {
  trial: "info",
  cancelled: "neutral",
};

const PAGE_DESCRIPTION =
  "Each product your organisation has started: its plan, its state, and when it renews or its trial ends.";

interface Row {
  subscription: Subscription;
  name: string;
  /** For a bundle: the products it switches on. Empty for a single product. */
  includes: string[];
  /** For a single product: the live bundles that also include it. */
  alsoIn: string[];
  /** Its plan and any scheduled change; null when the plan line could not be read. */
  plan: SubscriptionPlanSummary | null;
}

interface Loaded {
  rows: Row[];
  products: Product[];
  /** The plan line could not be read: the page says so and offers to try again. */
  plansFailed: boolean;
}

/**
 * The plan line is a second read: when it fails, the page still renders without it — and says so,
 * rather than leaving a dash where the plan and any scheduled change should be.
 */
async function loadSummaries(): Promise<SubscriptionPlanSummary[] | null> {
  try {
    const summaries = await listSubscriptionPlanSummaries();
    return Array.isArray(summaries) ? summaries : [];
  } catch {
    return null;
  }
}

async function loadRows(): Promise<Loaded> {
  const [subscriptions, products, read] = await Promise.all([
    listSubscriptions(),
    listProducts().catch(() => [] as Product[]),
    loadSummaries(),
  ]);
  const summaries = read ?? [];
  const names = Object.fromEntries(products.map((p) => [p.key, shortProductName(p.name)]));
  const plans = new Map(summaries.map((s) => [s.subscription_id, s]));
  const rows = subscriptions.map((subscription) => {
    const members = (subscription.entitled_products ?? []).filter((k) => k !== subscription.product_key);
    return {
      subscription,
      name: names[subscription.product_key] ?? subscription.product_key,
      includes: productNames(members, products),
      alsoIn: bundlesIncluding(subscription.product_key, subscriptions).map(
        (s) => names[s.product_key] ?? s.product_key,
      ),
      plan: plans.get(subscription.id) ?? null,
    };
  });
  return { rows, products, plansFailed: read === null };
}

/** Each product your organisation has started, its plan and state, and the actions its lifecycle allows. */
export function SubscriptionsPage() {
  const toast = useToast();
  const rows = useAsync(loadRows, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const [cancelling, setCancelling] = useState<Row | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [changingPlan, setChangingPlan] = useState<Row | null>(null);
  const [keepingPlan, setKeepingPlan] = useState<Row | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const cancel = useMutation(
    (row: Row) => cancelSubscription(row.subscription.id, cancelReason.trim() || undefined),
    {
      onSuccess: (_result, row) => {
        setCancelling(null);
        setCancelReason("");
        toast.success(`${row.name} cancelled.`);
        rows.reload();
      },
    },
  );

  if (rows.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Billing" title="Subscriptions" description={PAGE_DESCRIPTION} />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (rows.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Billing" title="Subscriptions" description={PAGE_DESCRIPTION} />
        <ErrorState title="Couldn't load subscriptions" message={problemMessage(rows.error)} onRetry={rows.reload} />
      </div>
    );
  }

  const subscriptionRows = rows.data.rows;
  const products = rows.data.products;
  const plansFailed = rows.data.plansFailed;

  const columns: Column<Row>[] = [
    {
      key: "product",
      header: "Product",
      mobile: "title",
      render: (row) => {
        const s = row.subscription;
        const finished = s.state === "cancelled";
        return (
          <div className="flex flex-col gap-1">
            <span className="font-semibold text-ink">{row.name}</span>
            {row.includes.length > 0 ? (
              <span className="text-xs text-muted">Includes {joinNames(row.includes)}</span>
            ) : null}
            {row.alsoIn.length > 0 && !finished ? (
              <span className="text-xs text-muted">Also included in {joinNames(row.alsoIn)}</span>
            ) : null}
            {s.dunning ? (
              <p className="text-xs font-medium text-late">
                Payment overdue — access ends {formatDate(s.dunning.grace_ends_at)}
                {s.dunning.next_attempt_at ? ` · Next attempt ${formatDate(s.dunning.next_attempt_at)}.` : ""}
              </p>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "plan",
      header: "Plan",
      render: (row) => <PlanCell plan={row.plan} subscription={row.subscription} />,
    },
    {
      key: "state",
      header: "State",
      mobile: "status",
      render: (row) => {
        const state = row.subscription.state;
        return <StatusBadge tone={STATE_TONE_OVERRIDE[state] ?? statusTone(state) ?? "neutral"} label={state} />;
      },
    },
    {
      key: "renewal",
      header: "Renewal",
      render: (row) => <StatusCell subscription={row.subscription} />,
    },
    {
      key: "period",
      header: "Current period",
      mobile: "hidden",
      render: (row) => {
        const s = row.subscription;
        return (
          <span className="text-ink-soft tabular-nums">
            {s.current_period_start ? `${formatDate(s.current_period_start)} – ${formatDate(s.current_period_end)}` : "—"}
          </span>
        );
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (row) => {
        const s = row.subscription;
        const finished = s.state === "cancelled";
        const scheduledBy = row.plan?.pending_scheduled_by ?? null;
        const unavailable = changePlanUnavailableReason(s, scheduledBy);
        const waiting = row.plan?.pending ?? null;
        return (
          <RowActions
            label={row.name}
            // Two inline at most: with Keep current plan beside them, three pills wrap onto two
            // lines on desktop and phone alike, so the third goes behind "More actions".
            inlineLimit={2}
            actions={[
              {
                label: "Change plan",
                onSelect: () => setChangingPlan(row),
                hidden: !canWrite || finished,
                disabled: unavailable !== null,
                disabledReason: unavailable ?? undefined,
              },
              {
                // A change the OpsGrids team scheduled (a negotiated price) is theirs to withdraw.
                label: "Keep current plan",
                onSelect: () => setKeepingPlan(row),
                hidden: !canWrite || waiting === null || scheduledBy === "opsgrids" || !canChangePlan(s),
              },
              { label: "Cancel", tone: "danger", onSelect: () => setCancelling(row), hidden: !canWrite || finished },
            ]}
          />
        );
      },
    },
  ];

  const subscriptionSelection: TableSelection<Row> = {
    selected,
    onToggle: (key, next) =>
      setSelected((prev) => {
        const nextSet = new Set(prev);
        if (next) nextSet.add(key);
        else nextSet.delete(key);
        return nextSet;
      }),
    onToggleAll: (next) => setSelected(next ? new Set(subscriptionRows.map((row) => row.subscription.id)) : new Set()),
    label: (row) => row.name,
  };

  // The selection's one bulk action is a CSV of rows already on screen — read-only, so it needs no
  // confirm. There is deliberately no bulk cancel or bulk plan change: each is a per-subscription
  // decision with its own preview, and cancel is final.
  function exportSelectedSubscriptions() {
    const chosen = subscriptionRows.filter((row) => selected.has(row.subscription.id));
    downloadCsv(
      "subscriptions.csv",
      [
        "Product",
        "Plan",
        "State",
        "Trial ends",
        "Current period start",
        "Current period end",
        "Scheduled plan",
        "Scheduled from",
      ],
      chosen.map((row) => {
        // A cancelled subscription never reaches a scheduled change; the export must not imply one.
        const waiting = row.subscription.state === "cancelled" ? null : (row.plan ?? null);
        return [
          row.name,
          row.plan?.current?.plan_name ?? "",
          row.subscription.state,
          row.subscription.state === "trial" ? (row.subscription.trial_ends_at ?? "") : "",
          row.subscription.current_period_start ?? "",
          row.subscription.current_period_end ?? "",
          waiting?.pending?.plan_name ?? "",
          waiting?.pending ? (waiting.pending_effective_at ?? "") : "",
        ];
      }),
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Subscriptions" description={PAGE_DESCRIPTION} />

      {plansFailed ? (
        <InlineAlert
          tone="warning"
          title="Plan details couldn't be loaded"
          action={
            <Button size="sm" variant="outline" onClick={rows.reload}>
              Try again
            </Button>
          }
        >
          The plan each product is on, and any scheduled plan change, aren't shown below. Everything else on this
          page is up to date.
        </InlineAlert>
      ) : null}

      <Card className="p-0">
        <DataTable
          rows={subscriptionRows}
          rowKey={(row) => row.subscription.id}
          columns={columns}
          mobileCard="auto"
          selection={subscriptionSelection}
          bulkActions={
            <Button variant="outline" size="sm" onClick={exportSelectedSubscriptions}>
              Export selected
            </Button>
          }
          emptyState={
            <EmptyState
              title="Nothing started yet"
              description="Pick a product from the Products page to begin a 14-day trial."
            />
          }
        />
      </Card>

      <ConfirmDialog
        open={cancelling !== null}
        onClose={() => {
          setCancelling(null);
          setCancelReason("");
          cancel.reset();
        }}
        onConfirm={() => cancelling && void cancel.mutate(cancelling)}
        title={`Cancel ${cancelling?.name ?? "this subscription"}?`}
        body={
          cancelling
            ? cancelBody(
                cancelling.name,
                cancelling.subscription,
                cancelling.plan?.pending?.plan_name ?? null,
                cancelling.plan?.pending_effective_at ?? null,
              )
            : ""
        }
        confirmLabel="Cancel subscription"
        pendingLabel="Cancelling…"
        pending={cancel.pending}
        {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
      >
        <TextArea
          label="Reason"
          optional
          value={cancelReason}
          onChange={(e) => setCancelReason(e.target.value)}
          maxLength={200}
          counterFrom={150}
          helper="Shown only in this organisation's own records."
        />
      </ConfirmDialog>


      {changingPlan ? (
        <ChangePlanDialog
          subscription={changingPlan.subscription}
          product={changingPlan.name}
          productNames={(keys) => productNames(keys, products)}
          onClose={() => setChangingPlan(null)}
          onChanged={(message) => {
            toast.success(message);
            rows.reload();
          }}
        />
      ) : null}

      {keepingPlan?.plan ? (
        <KeepPlanDialog
          subscription={keepingPlan.subscription}
          product={keepingPlan.name}
          summary={keepingPlan.plan}
          onClose={() => setKeepingPlan(null)}
          onKept={(message) => {
            toast.success(message);
            rows.reload();
          }}
          onStale={(message) => {
            setKeepingPlan(null);
            toast.info(message);
            rows.reload();
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * The plan in force, its price or cadence, and a scheduled change. When the plan line could not
 * be read, a dash — and, from the subscription row itself, that a change is scheduled.
 */
function PlanCell({ plan, subscription }: { plan: SubscriptionPlanSummary | null; subscription: Subscription }) {
  const current = plan?.current ?? null;
  if (plan === null || current === null) {
    const waiting = subscription.state !== "cancelled" && (subscription.pending_plan_version_id ?? null) !== null;
    return waiting ? (
      <span className="text-xs font-medium text-ink-soft">A plan change is scheduled</span>
    ) : (
      <span className="text-muted">—</span>
    );
  }
  const pending = pendingLine(plan.pending, plan.pending_effective_at, {
    scheduledBy: plan.pending_scheduled_by,
    state: subscription.state,
  });
  return (
    <div className="flex flex-col gap-1">
      <span className="font-medium text-ink">{planName(current)}</span>
      <span className="text-xs text-muted tabular-nums">
        {priceLine(current)}
        {current.negotiated ? " · agreed for your organisation" : ""}
      </span>
      {pending ? <span className="text-xs font-medium text-ink-soft">{pending}</span> : null}
    </div>
  );
}

/** The one date or fact that matters next, by state — never a trial date on a paid subscription. */
function StatusCell({ subscription }: { subscription: Subscription }) {
  const line = statusLine(subscription);
  return (
    <div className="flex flex-col gap-1">
      <span className={line.attention ? "font-medium text-ink" : "text-ink-soft"}>{line.primary}</span>
      {line.secondary ? <span className="text-xs text-muted">{line.secondary}</span> : null}
    </div>
  );
}
