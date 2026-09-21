import { Fragment, useMemo, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  RowActions,
  Select,
  Skeleton,
  Tag,
  TextArea,
  useToast,
} from "@opsgrids/ui";
import type { TagTone } from "@opsgrids/ui";

import { cancelSubscription, changeSubscriptionPlan, listPlans, listProducts, listSubscriptions } from "@/api";
import type { Plan, PlanVersion, Product, Subscription } from "@/api/types";
import { daysUntil, formatDate, formatMoney, intervalLabel } from "@/lib/money";
import { PageHeader } from "@/ui";
import { shortProductName } from "../lib/productName";

const WRITE_PERMISSION = "core.product.activation.write";

const STATE_TONE: Record<string, TagTone> = {
  trial: "info",
  active: "positive",
  past_due: "warning",
  suspended: "negative",
  cancelled: "neutral",
};

interface Row {
  subscription: Subscription;
  name: string;
}

async function loadRows(): Promise<Row[]> {
  const [subscriptions, products] = await Promise.all([
    listSubscriptions(),
    listProducts().catch(() => [] as Product[]),
  ]);
  const names = Object.fromEntries(products.map((p) => [p.key, shortProductName(p.name)]));
  return subscriptions.map((subscription) => ({
    subscription,
    name: names[subscription.product_key] ?? subscription.product_key,
  }));
}

/** Each product your organisation has started, its state, and the actions its lifecycle allows. */
export function SubscriptionsPage() {
  const toast = useToast();
  const rows = useAsync(loadRows, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const [cancelling, setCancelling] = useState<Row | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [changingPlan, setChangingPlan] = useState<Row | null>(null);

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
        <PageHeader eyebrow="Billing" title="Subscriptions" description="Each product your organisation has started, its state, and when the trial ends." />
        <Skeleton className="h-64 w-full rounded-card" />
      </div>
    );
  }

  if (rows.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Billing" title="Subscriptions" description="Each product your organisation has started, its state, and when the trial ends." />
        <ErrorState title="Couldn't load subscriptions" message={problemMessage(rows.error)} onRetry={rows.reload} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Subscriptions" description="Each product your organisation has started, its state, and when the trial ends." />

      {rows.data.length === 0 ? (
        <EmptyState
          title="Nothing started yet"
          description="Pick a product from the Products page to begin a 14-day trial."
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted">
                <th className="px-5 py-3 font-semibold">Product</th>
                <th className="px-5 py-3 font-semibold">State</th>
                <th className="px-5 py-3 font-semibold">Trial ends</th>
                <th className="px-5 py-3 font-semibold">Current period</th>
                <th className="px-5 py-3 text-right font-semibold">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.data.map((row) => {
                const s = row.subscription;
                const days = daysUntil(s.trial_ends_at);
                const finished = s.state === "cancelled";
                return (
                  <Fragment key={s.id}>
                    <tr className="border-t border-line">
                      <td className="px-5 py-3 font-semibold text-ink">{row.name}</td>
                      <td className="px-5 py-3">
                        <Tag tone={STATE_TONE[s.state] ?? "neutral"}>{s.state}</Tag>
                      </td>
                      <td className="px-5 py-3 text-ink-soft">
                        {s.trial_ends_at ? `${formatDate(s.trial_ends_at)}${days !== null && days >= 0 ? ` (${days} days)` : ""}` : "—"}
                      </td>
                      <td className="px-5 py-3 text-ink-soft">
                        {s.current_period_start ? `${formatDate(s.current_period_start)} – ${formatDate(s.current_period_end)}` : "—"}
                      </td>
                      <td className="px-5 py-3 text-right">
                        <RowActions
                          label={row.name}
                          actions={[
                            {
                              label: "Change plan",
                              onSelect: () => setChangingPlan(row),
                              hidden: !canWrite || finished,
                            },
                            {
                              label: "Cancel",
                              tone: "danger",
                              onSelect: () => setCancelling(row),
                              hidden: !canWrite || finished,
                            },
                          ]}
                        />
                      </td>
                    </tr>
                    {s.dunning ? (
                      <tr className="border-t border-line bg-late-soft/40">
                        <td colSpan={5} className="px-5 py-3">
                          <InlineAlert
                            tone="warning"
                            title={`Payment overdue — access ends ${formatDate(s.dunning.grace_ends_at)}`}
                          >
                            {s.dunning.next_attempt_at
                              ? `Next attempt ${formatDate(s.dunning.next_attempt_at)}.`
                              : null}
                          </InlineAlert>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}

      <ConfirmDialog
        open={cancelling !== null}
        onClose={() => {
          setCancelling(null);
          setCancelReason("");
          cancel.reset();
        }}
        onConfirm={() => cancelling && void cancel.mutate(cancelling)}
        title={`Cancel ${cancelling?.name ?? "this subscription"}?`}
        body="Cancellation is immediate — access to this product ends now, not at the end of the current period. This can't be undone."
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
          row={changingPlan}
          onClose={() => setChangingPlan(null)}
          onSaved={(name) => {
            toast.success(`${name} moved to the new plan.`);
            rows.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function planLabel(plan: Plan, version: PlanVersion): string {
  const flat = version.prices.find((p) => p.kind === "flat" && p.amount_minor !== null);
  const price = flat ? `${formatMoney(flat.amount_minor ?? 0, flat.currency)} ${intervalLabel(version.billing_interval)}` : "Price on request";
  return `${plan.name} — ${price}`;
}

function ChangePlanDialog({
  row,
  onClose,
  onSaved,
}: {
  row: Row;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const plans = useAsync(() => listPlans(row.subscription.product_key), [row.subscription.product_key]);
  const [selected, setSelected] = useState("");

  const options = useMemo(() => {
    if (plans.status !== "success") return [];
    const out: { value: string; label: string }[] = [];
    for (const plan of plans.data) {
      for (const version of plan.versions) {
        if (version.state !== "published") continue;
        if (version.id === row.subscription.plan_version_id) continue;
        out.push({ value: version.id, label: planLabel(plan, version) });
      }
    }
    return out;
  }, [plans, row.subscription.plan_version_id]);

  const [pickError, setPickError] = useState<string | undefined>(undefined);

  const save = useMutation((planVersionId: string) => changeSubscriptionPlan(row.subscription.id, planVersionId), {
    onSuccess: () => {
      onClose();
      onSaved(row.name);
    },
  });

  function submit() {
    setPickError(undefined);
    if (!selected) {
      setPickError("Choose a plan to change to.");
      return;
    }
    void save.mutate(selected);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Change ${row.name}'s plan`}
      description="Takes effect immediately; the old plan is kept in the subscription's history."
      submitLabel="Change plan"
      pendingLabel="Saving…"
      pending={save.pending}
      {...(save.error ? { error: problemMessage(save.error) } : {})}
    >
      {plans.status === "loading" ? <Skeleton className="h-11 w-full" /> : null}
      {plans.status === "error" ? (
        <ErrorState title="Couldn't load plans" message={problemMessage(plans.error)} onRetry={plans.reload} />
      ) : null}
      {plans.status === "success" ? (
        options.length === 0 ? (
          <p className="text-sm text-muted">There is no other published plan for this product yet.</p>
        ) : (
          <Select
            label="New plan"
            placeholder="Choose a plan"
            options={options}
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            error={pickError}
          />
        )
      ) : null}
    </FormDialog>
  );
}
