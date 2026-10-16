import { useState } from "react";

import type { CollectionsProvider, MerchantAccountIn } from "@/api/types";
import { PROVIDER_NAME, choosesEnvironment } from "@/lib/paymentProviders";
import { Button, InlineAlert, RadioGroup, TextField } from "@/ui";

interface MerchantAccountFormProps {
  provider: CollectionsProvider;
  /** The key id / App ID / Client ID already connected, when replacing credentials. */
  initialKeyId: string;
  /** The non-secret settings already connected (PhonePe's client version and webhook username). */
  initialConfig?: Record<string, string | number | boolean> | undefined;
  /** A connected Cashfree or PhonePe account's environment is fixed: switching is disconnect, then connect. */
  fixedMode: "sandbox" | "production" | null;
  liveAllowed: boolean;
  pending: boolean;
  error: string | null;
  onSubmit: (input: MerchantAccountIn) => void;
  onCancel: () => void;
}

const LABELS: Record<CollectionsProvider, { keyId: string; keySecret: string }> = {
  razorpay: { keyId: "Key id", keySecret: "Key secret" },
  cashfree: { keyId: "App ID", keySecret: "Secret Key" },
  phonepe: { keyId: "Client ID", keySecret: "Client Secret" },
};

/**
 * The credentials of one provider account, typed once and sent straight to the payment service,
 * which seals them; the parent clears this form the moment the save lands, so a secret never
 * outlives the request in the page's memory. Razorpay takes a key id, a key secret and the
 * webhook secret the merchant set in its dashboard; Cashfree an App ID, a Secret Key (which also
 * signs its webhooks, so there is no separate webhook secret) and the environment — an App ID does
 * not say whether it is Sandbox or Production. PhonePe takes the Client ID, Client Secret and
 * client version it issued, the webhook username and password the merchant set on its webhook,
 * and the environment.
 */
export function MerchantAccountForm({
  provider,
  initialKeyId,
  initialConfig,
  fixedMode,
  liveAllowed,
  pending,
  error,
  onSubmit,
  onCancel,
}: MerchantAccountFormProps) {
  const [keyId, setKeyId] = useState(initialKeyId);
  const [keySecret, setKeySecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [clientVersion, setClientVersion] = useState(String(initialConfig?.client_version ?? ""));
  const [webhookUsername, setWebhookUsername] = useState(String(initialConfig?.webhook_username ?? ""));
  const [mode, setMode] = useState<"sandbox" | "production">(fixedMode ?? "sandbox");
  const name = PROVIDER_NAME[provider];
  const labels = LABELS[provider];

  function submit() {
    if (provider === "cashfree") {
      onSubmit({ provider, key_id: keyId.trim(), key_secret: keySecret, mode });
    } else if (provider === "phonepe") {
      onSubmit({
        provider,
        key_id: keyId.trim(),
        key_secret: keySecret,
        webhook_secret: webhookSecret,
        mode,
        config: { client_version: Number(clientVersion.trim()), webhook_username: webhookUsername.trim() },
      });
    } else {
      onSubmit({ provider, key_id: keyId.trim(), key_secret: keySecret, webhook_secret: webhookSecret });
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      aria-label={`${name} credentials`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {error ? <InlineAlert tone="danger" title={error} /> : null}
      <TextField label={labels.keyId} value={keyId} onChange={(e) => setKeyId(e.target.value)} autoComplete="off" required />
      <TextField
        label={labels.keySecret}
        type="password"
        value={keySecret}
        onChange={(e) => setKeySecret(e.target.value)}
        autoComplete="new-password"
        required
      />
      {provider === "phonepe" ? (
        <TextField
          label="Client version"
          helper="The number PhonePe shows with your Client ID."
          inputMode="numeric"
          pattern="[0-9]{1,6}"
          value={clientVersion}
          onChange={(e) => setClientVersion(e.target.value)}
          autoComplete="off"
          required
        />
      ) : null}
      {choosesEnvironment(provider) ? (
        <RadioGroup
          legend="Environment"
          name={`${provider}-environment`}
          value={mode}
          onChange={(value) => setMode(value === "production" ? "production" : "sandbox")}
          {...(fixedMode !== null ? { helper: "A connected account's environment cannot change: disconnect, then connect the other one." } : {})}
          options={[
            {
              value: "sandbox",
              label: "Sandbox",
              description: "Test payments only — no real money moves.",
              disabled: fixedMode !== null && fixedMode !== "sandbox",
            },
            {
              value: "production",
              label: "Production",
              description: liveAllowed ? `Real payments, into your ${name} account.` : "Not enabled on this platform yet.",
              disabled: !liveAllowed || (fixedMode !== null && fixedMode !== "production"),
            },
          ]}
        />
      ) : null}
      {provider === "phonepe" ? (
        <TextField
          label="Webhook username"
          helper="The username you set on the webhook in PhonePe's dashboard."
          value={webhookUsername}
          onChange={(e) => setWebhookUsername(e.target.value)}
          autoComplete="off"
          required
        />
      ) : null}
      {provider !== "cashfree" ? (
        <TextField
          label={provider === "phonepe" ? "Webhook password" : "Webhook secret"}
          helper={
            provider === "phonepe"
              ? "The password you set on the webhook in PhonePe's dashboard."
              : "The secret you set on the webhook in Razorpay's dashboard."
          }
          type="password"
          value={webhookSecret}
          onChange={(e) => setWebhookSecret(e.target.value)}
          autoComplete="new-password"
          required
        />
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={pending}>
          Save
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
