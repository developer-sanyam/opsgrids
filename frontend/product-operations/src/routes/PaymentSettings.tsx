import { useState } from "react";
import type { ReactNode } from "react";

import {
  getMerchantAccount,
  getPaymentReconciliation,
  removeMerchantAccount,
  saveMerchantAccount,
  verifyMerchantAccount,
} from "@/api/client";
import type { CollectionsProvider, MerchantAccountIn, MerchantAccountOut, ReconciliationOut } from "@/api/types";
import { MerchantAccountForm } from "@/forms/MerchantAccountForm";
import { MerchantWebhookPanel } from "@/forms/MerchantWebhookPanel";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { needsOneTimeCodeSession } from "@/lib/oneTimeCode";
import {
  CONNECTION_BADGE,
  choosesEnvironment,
  connectionErrorHint,
  mismatchLabel,
  modeLabel,
  PROVIDER_NAME,
  providerName,
  wireFor,
} from "@/lib/paymentProviders";
import { ApiError, Can, problemMessage, problemSlug, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  GoBackButton,
  InlineAlert,
  PageHeader,
  RadioGroup,
  SectionHeader,
  Skeleton,
} from "@/ui";
import { StatusBadge, useToast } from "@opsgrids/ui";

/** Changing where a business's money goes needs a session that signed in with a one-time code. */
const ONE_TIME_CODE_MESSAGE =
  "Connecting, changing, checking or disconnecting the payment account needs a session signed in with a one-time code. Sign out, sign in again with your authenticator code, and try again — or ask an admin who has two-step sign-in turned on.";

function errorText(error: unknown): string {
  return needsOneTimeCodeSession(error) ? ONE_TIME_CODE_MESSAGE : problemMessage(error);
}

/** The server refused a disconnect because the provider may still move money for the account. */
function disconnectBlocked(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && problemSlug(error.problem) === "disconnect-blocked";
}

const PROVIDER_OPTIONS = [
  {
    value: "razorpay",
    label: PROVIDER_NAME.razorpay,
    description: "Customers pay in Razorpay's checkout; you connect your key id, key secret and a webhook secret.",
  },
  {
    value: "cashfree",
    label: PROVIDER_NAME.cashfree,
    description: "Customers pay in Cashfree's checkout; you connect your App ID and Secret Key. Each order needs the customer's phone.",
  },
  {
    value: "phonepe",
    label: PROVIDER_NAME.phonepe,
    description:
      "Customers pay on PhonePe's checkout page, opened in a new tab; you connect your Client ID, Client Secret, client version and a webhook username and password.",
  },
  {
    value: "payu",
    label: PROVIDER_NAME.payu,
    description:
      "Customers pay on PayU's checkout page, opened in a new tab; you connect your merchant key and salt. Each order needs the customer's phone.",
  },
];

const KEY_LABELS: Record<CollectionsProvider, { keyId: string; keySecret: string }> = {
  razorpay: { keyId: "Key id", keySecret: "Key secret" },
  cashfree: { keyId: "App ID", keySecret: "Secret Key" },
  phonepe: { keyId: "Client ID", keySecret: "Client Secret" },
  payu: { keyId: "Merchant key", keySecret: "Salt" },
};

function asProvider(value: string): CollectionsProvider {
  return value === "cashfree" || value === "phonepe" || value === "payu" ? value : "razorpay";
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function AccountDetails({ account }: { account: MerchantAccountOut }) {
  const provider = asProvider(account.provider ?? "razorpay");
  const labels = KEY_LABELS[provider];
  return (
    <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
      <Detail label={labels.keyId}>
        <span className="font-mono text-ink break-all">{account.key_id}</span>
      </Detail>
      <Detail label={choosesEnvironment(provider) ? "Environment" : "Mode"}>
        <StatusBadge
          tone={account.mode === "live" ? "success" : "warning"}
          label={modeLabel(account.provider, account.mode)}
        />
      </Detail>
      <Detail label={labels.keySecret}>
        <span className="font-mono text-ink">{account.key_secret_masked}</span>
      </Detail>
      {provider === "phonepe" ? (
        <>
          <Detail label="Client version">
            <span className="font-mono text-ink">{String(account.config?.client_version ?? "")}</span>
          </Detail>
          <Detail label="Webhook username">
            <span className="font-mono text-ink break-all">{String(account.config?.webhook_username ?? "")}</span>
          </Detail>
        </>
      ) : null}
      {provider === "cashfree" || provider === "payu" ? null : (
        <Detail label={provider === "phonepe" ? "Webhook password" : "Webhook secret"}>
          <span className="font-mono text-ink">{account.webhook_secret_masked}</span>
        </Detail>
      )}
      {account.rotated_at ? (
        <Detail label="Credentials last replaced">
          <span className="tnum text-ink">{formatDate(account.rotated_at.slice(0, 10))}</span>
        </Detail>
      ) : null}
    </dl>
  );
}

function ConnectionStatus({
  account,
  checkable,
  checking,
  onCheck,
}: {
  account: MerchantAccountOut;
  /** Whether the provider can be asked at all: not while this platform runs it on the stub. */
  checkable: boolean;
  checking: boolean;
  onCheck: () => void;
}) {
  const state = account.connection_state ?? "unverified";
  const badge = CONNECTION_BADGE[state];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm text-muted">Connection</span>
        <StatusBadge tone={badge.tone} label={badge.label} />
        {account.verified_at ? (
          <span className="tnum text-xs text-muted">Checked {formatDateTime(account.verified_at)}</span>
        ) : null}
        <Can permission="operations.payments.merchant_account.write" scope="ORG">
          <Button size="sm" variant="outline" loading={checking} disabled={!checkable} onClick={onCheck}>
            Check connection
          </Button>
        </Can>
      </div>
      {!checkable ? (
        <p className="text-sm text-ink-soft">
          These credentials can be checked with {providerName(account.provider)} once the platform turns its{" "}
          {providerName(account.provider)} connection on. Until then nothing reaches {providerName(account.provider)}, so
          nothing can say whether they work.
        </p>
      ) : state === "failed" ? (
        <p className="text-sm text-ink-soft">{connectionErrorHint(account.provider, account.last_error_code)}</p>
      ) : state === "unverified" ? (
        <p className="text-sm text-ink-soft">
          Not checked with {providerName(account.provider)} yet — check the connection before taking payments.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The organization's own payment-provider account (ADR-0024 A4, ADR-0033): Razorpay or Cashfree,
 * chosen when connecting and fixed while connected (switching is disconnect, then connect). Here
 * an admin connects it, replaces its credentials, checks them with the provider, reads where the
 * provider's webhooks should go, disconnects it, and compares recent online payments with the
 * provider's records.
 *
 * Secrets are typed once and sent straight to the payment service, which seals them; from then on
 * only a masked hint ever comes back. Every change also needs a session that signed in with a
 * one-time code — the server refuses one that did not, and this screen says how to fix that.
 */
export function PaymentSettingsPage() {
  const toast = useToast();
  const canRead = useCan("operations.payments.merchant_account.read", "ORG");
  const canReconcile = useCan("operations.payments.reconciliation.read", "ORG");
  const state = useAsync(() => (canRead ? getMerchantAccount() : Promise.resolve(null)), [canRead]);

  const [chosen, setChosen] = useState<CollectionsProvider>("razorpay");
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [report, setReport] = useState<ReconciliationOut | null>(null);

  const verify = useMutation(() => verifyMerchantAccount(), {
    onSuccess: (checked) => {
      toast.success(`${providerName(checked.provider)} accepted these credentials.`);
      state.reload();
    },
    // A refusal is recorded on the account too: show what the server now says.
    onError: () => state.reload(),
  });
  const save = useMutation((input: MerchantAccountIn) => saveMerchantAccount(input), {
    onSuccess: (saved) => {
      setEditing(false);
      // A refusal of the old credentials says nothing about these.
      verify.reset();
      const checkable = saved.provider ? wireFor(saved, saved.provider) === "live" : false;
      toast.success(
        checkable
          ? `${providerName(saved.provider)} account saved. Check the connection next.`
          : `${providerName(saved.provider)} account saved.`,
      );
      state.reload();
    },
  });
  const remove = useMutation((force: boolean) => removeMerchantAccount({ force }), {
    onSuccess: () => {
      setRemoving(false);
      verify.reset();
      toast.success("Payment account disconnected.");
      state.reload();
    },
  });
  // Refused because money may still move: the operator now chooses, knowingly, to go ahead.
  const blocked = disconnectBlocked(remove.error);
  const reconcile = useMutation(() => getPaymentReconciliation(), { onSuccess: (r) => setReport(r) });

  if (!canRead) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Settings" title="Online payments" />
        <Card className="p-6">
          <ErrorState
            tone="neutral"
            message="You don't have access to this organization's payment settings."
            action={<GoBackButton />}
          />
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
  const provider: CollectionsProvider = account.configured && account.provider ? account.provider : chosen;
  const name = PROVIDER_NAME[provider];
  const fixedMode =
    account.configured && choosesEnvironment(provider) ? (account.mode === "live" ? "production" : "sandbox") : null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Settings"
        title="Online payments"
        description="Your customers pay orders online through your own payment-provider account. Money goes straight to you."
      />

      {wireFor(account, provider) === "stub" ? (
        <InlineAlert tone="warning" title={`${name} payments are not live on this platform yet`}>
          Checkouts can be opened and tracked, but no request reaches {name} until the platform turns its {name} connection on.
        </InlineAlert>
      ) : null}

      <Card className="p-6 flex flex-col gap-4">
        <SectionHeader title={`${name} account`} subtitle={account.configured ? "Connected" : "Not connected"} />

        <RadioGroup
          legend="Payment provider"
          name="payment-provider"
          value={provider}
          onChange={(value) => {
            setChosen(asProvider(value));
            setEditing(false);
          }}
          {...(account.configured
            ? { helper: "To switch provider, disconnect this account, then connect the other one. It gets a new webhook URL." }
            : {})}
          options={PROVIDER_OPTIONS.map((option) => ({ ...option, disabled: account.configured }))}
        />

        {account.configured ? (
          <>
            <AccountDetails account={account} />
            <ConnectionStatus
              account={account}
              checkable={wireFor(account, provider) === "live"}
              checking={verify.pending}
              onCheck={() => void verify.mutate(undefined)}
            />
            {verify.error ? <InlineAlert tone="danger" title={errorText(verify.error)} /> : null}
          </>
        ) : (
          <p className="text-sm text-muted">
            Connect a {name} account to let customers pay orders online.{" "}
            {account.live_keys_allowed
              ? ""
              : choosesEnvironment(provider)
                ? "Only Sandbox accounts can be connected on this platform for now."
                : "Only test-mode keys (rzp_test_…) can be connected on this platform for now."}
          </p>
        )}

        {editing ? (
          <MerchantAccountForm
            provider={provider}
            initialKeyId={account.configured ? (account.key_id ?? "") : ""}
            initialConfig={account.configured ? account.config : undefined}
            fixedMode={fixedMode}
            liveAllowed={account.live_keys_allowed}
            pending={save.pending}
            error={save.error ? errorText(save.error) : null}
            onSubmit={(input) => void save.mutate(input)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <Can permission="operations.payments.merchant_account.write" scope="ORG">
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => {
                  save.reset();
                  verify.reset();
                  setEditing(true);
                }}
              >
                {account.configured ? "Replace credentials" : `Connect ${name}`}
              </Button>
              {account.configured ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    remove.reset();
                    setRemoving(true);
                  }}
                >
                  Disconnect
                </Button>
              ) : null}
            </div>
          </Can>
        )}
      </Card>

      {account.configured ? (
        <Card className="p-6 flex flex-col gap-4">
          <SectionHeader title="Webhook" subtitle={`How ${name} tells this platform about payments.`} />
          <MerchantWebhookPanel account={account} />
        </Card>
      ) : null}

      {account.configured && canReconcile ? (
        <Card className="p-6 flex flex-col gap-4">
          <SectionHeader
            title="Reconciliation"
            subtitle={`Compares recent online payments with ${name}'s own records. It changes nothing.`}
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
                          {formatMoney(r.amount_minor, r.currency)} · ours: {r.state} · {name}: {r.provider_status ?? "no payment"}
                        </span>
                        <span className="text-muted">
                          {r.lookup_failed
                            ? `${name} could not be asked — try again.`
                            : r.mismatches.map((m) => mismatchLabel(provider, m)).join(" · ")}
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
        onConfirm={() => void remove.mutate(blocked)}
        title={blocked ? `Disconnect ${name} anyway?` : `Disconnect ${name}?`}
        body={
          blocked
            ? `${name} can still move money for this account. Once it is disconnected, nothing it reports about those payments or refunds is accepted here: an order paid after this stays unpaid, and a refund that fails stays refunded. You would settle each one in ${name}'s own dashboard.`
            : `Customers can no longer pay orders online, and its webhooks stop being accepted. Payments already made stay recorded. Connecting again — ${name} or another provider — gives a new webhook URL.`
        }
        confirmLabel={blocked ? "Disconnect anyway" : "Disconnect"}
        pendingLabel="Disconnecting…"
        tone="danger"
        pending={remove.pending}
        {...(remove.error && !blocked ? { error: errorText(remove.error) } : {})}
      >
        {blocked ? (
          // No "Try again" here: the only way on is the explicit "Disconnect anyway" above.
          <InlineAlert tone="warning" title="Not everything on this account has settled">
            {problemMessage(remove.error)}
          </InlineAlert>
        ) : undefined}
      </ConfirmDialog>
    </div>
  );
}
