import { useMemo, useRef, useState } from "react";

import { useMutation } from "@opsgrids/client";
import { Button, Dialog, FormDialog, Select, TextArea } from "@opsgrids/ui";

import { postOverride } from "@/api/client";
import type { CatalogBundle } from "@/api/client";
import type { OrganizationRow, OverrideRequest, OverrideResult, Subscription } from "@/api/types";
import { referenceOf } from "@/lib/errors";
import { formatMoney } from "@/lib/money";
import { Code, DelegationNote } from "./parts";
import { failureText } from "./text";

interface MigratePlanDialogProps {
  org: OrganizationRow;
  subscriptions: Subscription[];
  catalog: CatalogBundle;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/**
 * Pin a subscription to a different plan version. The old version is preserved for history, so
 * past invoices still reconstruct; only an active or past-due subscription can move, and only to
 * a published version that is public or restricted to this very customer.
 *
 * An account with nothing to migrate gets a sentence and a Close rather than a form it could not
 * submit; a subscription whose product has no other published version says so when submitted,
 * since a submit that silently did nothing would be worse than the refusal.
 */
export function MigratePlanDialog({ org, subscriptions, catalog, onClose, onDone }: MigratePlanDialogProps) {
  const migratable = subscriptions.filter((s) => s.state === "active" || s.state === "past_due");
  const [subscriptionId, setSubscriptionId] = useState(migratable[0]?.subscription_id ?? "");
  const [targetId, setTargetId] = useState("");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [noTarget, setNoTarget] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const subscriptionRef = useRef<HTMLSelectElement>(null);

  const selected = migratable.find((s) => s.subscription_id === subscriptionId) ?? migratable[0];

  const targetVersions = useMemo(() => {
    if (!selected) return [];
    return catalog.versions.filter(
      (v) =>
        v.product_key === selected.product_key &&
        v.state === "published" &&
        v.plan_version_id !== selected.plan_version_id &&
        (v.restricted_to_org_id === null || v.restricted_to_org_id === org.organization_id),
    );
  }, [catalog.versions, selected, org.organization_id]);

  const effectiveTarget = targetVersions.find((v) => v.plan_version_id === targetId)
    ? targetId
    : (targetVersions[0]?.plan_version_id ?? "");

  const run = useMutation((request: OverrideRequest) => postOverride(request), {
    onSuccess: (result) => {
      onClose();
      onDone(result);
    },
  });

  if (migratable.length === 0) {
    return (
      <Dialog
        open
        onClose={onClose}
        title={`Migrate plan — ${org.name}`}
        description="Pins the subscription to a new plan version. The old version is preserved for history."
        footer={
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        }
      >
        <p className="text-sm text-muted">
          This account has no active subscription to migrate. Reactivate it first.
        </p>
      </Dialog>
    );
  }

  function submit() {
    setReasonError(undefined);
    setNoTarget(false);
    if (selected === undefined) return;
    if (effectiveTarget === "") {
      setNoTarget(true);
      subscriptionRef.current?.focus();
      return;
    }
    if (reason.trim().length === 0) {
      setReasonError("Give a reason — it goes into the audit log.");
      reasonRef.current?.focus();
      return;
    }
    void run.mutate({
      kind: "plan_migration",
      organization_id: org.organization_id,
      subscription_id: selected.subscription_id,
      target_plan_version_id: effectiveTarget,
      reason: reason.trim(),
    });
  }

  const serverFailure = failureText(run.error, run.fieldErrors, ["subscription_id", "target_plan_version_id", "reason"]);

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Migrate plan — ${org.name}`}
      description="Pins the subscription to a new plan version. The old version is preserved for history."
      submitLabel="Migrate plan"
      pendingLabel="Migrating…"
      pending={run.pending}
      initialFocusRef={subscriptionRef}
      error={noTarget ? "No other published version is available for this product." : serverFailure}
      reference={noTarget ? undefined : referenceOf(run.error)}
    >
      <Select
        ref={subscriptionRef}
        label="Subscription"
        options={migratable.map((s) => ({
          value: s.subscription_id,
          label: `${s.product_name} · ${s.plan_name} (v${s.plan_version_no})`,
        }))}
        value={selected?.subscription_id ?? ""}
        onChange={(event) => {
          setSubscriptionId(event.target.value);
          setTargetId("");
          setNoTarget(false);
        }}
        error={run.fieldErrors.subscription_id}
      />

      {targetVersions.length === 0 ? (
        <p className="rounded-tile bg-ground px-3 py-2.5 text-sm text-muted">
          No other published version is available for this product.
        </p>
      ) : (
        <Select
          label="Target plan version"
          options={targetVersions.map((v) => ({
            value: v.plan_version_id,
            label: `${v.plan_name} · v${v.version_no} · ${formatMoney(
              v.prices.find((p) => p.kind === "flat")?.amount_minor ?? 0,
              v.currency,
            )}/mo`,
          }))}
          value={effectiveTarget}
          onChange={(event) => setTargetId(event.target.value)}
          error={run.fieldErrors.target_plan_version_id}
        />
      )}

      <TextArea
        ref={reasonRef}
        label="Reason (required)"
        rows={2}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="e.g. Upgrade to Scale at renewal"
        error={reasonError ?? run.fieldErrors.reason}
      />

      <DelegationNote>
        POSTs <Code>override.plan_migration</Code> to <Code>/api/v1/admin/overrides</Code> →
        subscription-service previews the money delta and pins the new version. The prior pin is
        preserved so past invoices still reconstruct.
      </DelegationNote>
    </FormDialog>
  );
}
