import { useState } from "react";

import { getMerchantAccount, getPaymentReconciliation, removeMerchantAccount, saveMerchantAccount } from "@/api/client";
import type { MerchantAccountOut, ReconciliationOut } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { Button, Card, ConfirmDialog, ErrorState, InlineAlert, PageHeader, SectionHeader, Skeleton, TextField } from "@/ui";
import { StatusBadge, useToast } from "@opsgrids/ui";

const MISMATCH_LABEL: Record<string, string> = {
  captured_at_provider_only: "Paid at Razorpay, not recorded here",
  not_captured_at_provider: "Recorded as paid here, not paid at Razorpay",
  payment_id_mismatch: "Different payment id",
  amount_mismatch: "Different amount",
  currency_mismatch: "Different currency",
  refund_mismatch: "Different refunded amount",
};

function webhookUrl(path: string): string {
  return `${window.location.origin}${path}`;
}

/**
 * The organization's own Razorpay account (ADR-0024 A4, tenant payment collection): connect it,
 * rotate its credentials, remove it, and compare recent online payments with Razorpay's records.
 *
 * The key secret and webhook secret are typed here once and sent straight to the payment service,
 * which seals them; from then on only a masked hint ever comes back. The key id is public by
 * design — Razorpay's Checkout is opened in a customer's browser with it.
 */
export function PaymentSettingsPage() {
  const toast = useToast();
  const canRead = useCan("operations.payments.merchant_account.read", "ORG");
  const canReconcile = useCan("operations.payments.reconciliation.read", "ORG");
  const state = useAsync(() => (canRead ? getMerchantAccount() : Promise.resolve(null)), [canRead]);

  const [editing, setEditing] = useState(false);
  const [keyId, setKeyId] = useState("");
  const [keySecret, setKeySecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [removing, setRemoving] = useState(false);
  const [report, setReport] = useState<ReconciliationOut | null>(null);

  const save = useMutation(
    () => saveMerchantAccount({ provider: "razorpay", key_id: keyId.trim(), key_secret: keySecret, webhook_secret: webhookSecret }),
    {
      onSuccess: () => {
        // The secrets leave the page's memory the moment they are saved.
        setKeySecret("");
        setWebhookSecret("");
        setEditing(false);
        toast.success("Razorpay account saved.");
        state.reload();
      },
    },
  );
  const remove = useMutation(() => removeMerchantAccount(), {
    onSuccess: () => {
      setRemoving(false);
      toast.success("Razorpay account disconnected.");
      state.reload();
    },
  });
  const reconcile = useMutation(() => getPaymentReconciliation(), { onSuccess: (r) => setReport(r) });

  function startEditing(account: MerchantAccountOut | null) {
    setKeyId(account?.key_id ?? "");
    setKeySecret("");
    setWebhookSecret("");
    save.reset();
    setEditing(true);
  }

  if (!canRead) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Settings" title="Online payments" />
        <Card className="p-6">
          <ErrorState message="You don't have access to this organization's payment settings." />
        </Card>
      </div>
    );
  }

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Settings" title="Online payments" />
        <Card className="p-6">
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (state.status === "error" || state.data === null) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Settings" title="Online payments" />
        <Card className="p-6">
          <ErrorState
            message={state.status === "error" ? problemMessage(state.error) : "Unavailable."}
            {...(state.status === "error" ? { onRetry: state.reload } : {})}
          />
        </Card>
      </div>
    );
  }

  const account = state.data;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Settings"
        title="Online payments"
        description="Your customers pay orders online through your own Razorpay account. Money goes straight to you."
      />

      {account.wire === "stub" ? (
        <InlineAlert tone="warning" title="Payments are not live on this platform yet">
          Checkouts can be opened and tracked, but no request reaches Razorpay until the platform turns its provider
          connection on.
        </InlineAlert>
      ) : null}

      <Card className="p-6 flex flex-col gap-4">
        <SectionHeader
          title="Razorpay account"
          subtitle={account.configured ? "Connected" : "Not connected"}
        />
        {account.configured ? (
          <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <dt className="text-muted">Key id</dt>
              <dd className="font-mono text-ink break-all">{account.key_id}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-muted">Mode</dt>
              <dd>
                <StatusBadge tone={account.mode === "live" ? "success" : "warning"} label={account.mode === "live" ? "Live" : "Test"} />
              </dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-muted">Key secret</dt>
              <dd className="font-mono text-ink">{account.key_secret_masked}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-muted">Webhook secret</dt>
              <dd className="font-mono text-ink">{account.webhook_secret_masked}</dd>
            </div>
            {account.webhook_path ? (
              <div className="flex flex-col gap-1 sm:col-span-2">
                <dt className="text-muted">Webhook URL — add this in Razorpay → Settings → Webhooks</dt>
                <dd className="font-mono text-ink break-all">{webhookUrl(account.webhook_path)}</dd>
              </div>
            ) : null}
            <div className="flex flex-col gap-1 sm:col-span-2">
              <dt className="text-muted">Events to enable on that webhook</dt>
              <dd className="font-mono text-xs text-ink-soft">{account.webhook_events.join(", ")}</dd>
            </div>
            {account.rotated_at ? (
              <div className="flex flex-col gap-1">
                <dt className="text-muted">Credentials last replaced</dt>
                <dd className="tnum text-ink">{formatDate(account.rotated_at.slice(0, 10))}</dd>
              </div>
            ) : null}
          </dl>
        ) : (
          <p className="text-sm text-muted">
            Connect a Razorpay account to let customers pay orders online.{" "}
            {account.live_keys_allowed ? "" : "Only test-mode keys (rzp_test_…) can be connected on this platform for now."}
          </p>
        )}

        {editing ? (
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void save.mutate(undefined);
            }}
          >
            {save.error ? <InlineAlert tone="danger" title={problemMessage(save.error)} /> : null}
            <TextField label="Key id" value={keyId} onChange={(e) => setKeyId(e.target.value)} autoComplete="off" required />
            <TextField
              label="Key secret"
              type="password"
              value={keySecret}
              onChange={(e) => setKeySecret(e.target.value)}
              autoComplete="new-password"
              required
            />
            <TextField
              label="Webhook secret"
              helper="The secret you set on the webhook in Razorpay's dashboard."
              type="password"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              autoComplete="new-password"
              required
            />
            <div className="flex gap-2">
              <Button type="submit" loading={save.pending}>
                Save
              </Button>
              <Button type="button" variant="outline" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <Can permission="operations.payments.merchant_account.write" scope="ORG">
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => startEditing(account.configured ? account : null)}>
                {account.configured ? "Replace credentials" : "Connect Razorpay"}
              </Button>
              {account.configured ? (
                <Button variant="outline" onClick={() => setRemoving(true)}>
                  Disconnect
                </Button>
              ) : null}
            </div>
          </Can>
        )}
      </Card>

      {account.configured && canReconcile ? (
        <Card className="p-6 flex flex-col gap-4">
          <SectionHeader
            title="Reconciliation"
            subtitle="Compares recent online payments with Razorpay's own records. It changes nothing."
          />
          {reconcile.error ? <InlineAlert tone="danger" title={problemMessage(reconcile.error)} /> : null}
          <div>
            <Button variant="outline" loading={reconcile.pending} onClick={() => void reconcile.mutate(undefined)}>
              Run check
            </Button>
          </div>
          {report ? (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-ink" role="status">
                Checked {report.checked}, {report.mismatched} {report.mismatched === 1 ? "difference" : "differences"}
                {report.unchecked > 0 ? `, ${report.unchecked} could not be checked` : ""}.
              </p>
              {report.rows.filter((r) => r.mismatches.length > 0 || r.lookup_failed).length > 0 ? (
                <ul className="flex flex-col gap-2" aria-label="Differences">
                  {report.rows
                    .filter((r) => r.mismatches.length > 0 || r.lookup_failed)
                    .map((r) => (
                      <li key={r.charge_id} className="rounded-tile border border-line p-3 text-sm flex flex-col gap-1">
                        <span className="font-semibold text-ink">
                          {formatMoney(r.amount_minor, r.currency)} · ours: {r.state} · Razorpay: {r.provider_status ?? "no payment"}
                        </span>
                        <span className="text-muted">
                          {r.lookup_failed
                            ? "Razorpay could not be asked — try again."
                            : r.mismatches.map((m) => MISMATCH_LABEL[m] ?? m).join(" · ")}
                        </span>
                        {r.provider_order_id ? (
                          <span className="font-mono text-xs text-ink-soft">{r.provider_order_id}</span>
                        ) : null}
                      </li>
                    ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}

      <ConfirmDialog
        open={removing}
        onClose={() => setRemoving(false)}
        onConfirm={() => void remove.mutate(undefined)}
        title="Disconnect Razorpay?"
        body="Customers can no longer pay orders online, and its webhooks stop being accepted. Payments already made stay recorded. Connecting again gives a new webhook URL."
        confirmLabel="Disconnect"
        pendingLabel="Disconnecting…"
        tone="danger"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
