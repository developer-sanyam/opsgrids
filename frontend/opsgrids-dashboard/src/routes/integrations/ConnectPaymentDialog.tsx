import { useState } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, RadioGroup, TextField } from "@opsgrids/ui";

import { saveMerchantAccount } from "@/api/integrations";
import type { CollectionsProvider, MerchantAccountIn, MerchantAccountOut } from "@/api/integrations";
import type { Connector } from "@/lib/integrations";

interface ConnectPaymentDialogProps {
  connector: Connector;
  onClose: () => void;
  onConnected: (account: MerchantAccountOut) => void;
}

const PROVIDER_LABELS: Record<string, { keyId: string; keySecret: string }> = {
  razorpay: { keyId: "Key ID", keySecret: "Key Secret" },
  cashfree: { keyId: "App ID", keySecret: "Secret Key" },
  phonepe: { keyId: "Client ID", keySecret: "Client Secret" },
  paytm: { keyId: "Merchant ID (MID)", keySecret: "Merchant Key" },
  payu: { keyId: "Merchant Key", keySecret: "Merchant Salt" },
  stripe: { keyId: "Account ID / Publishable Key", keySecret: "Secret Key" },
};

export function ConnectPaymentDialog({
  connector,
  onClose,
  onConnected,
}: ConnectPaymentDialogProps) {
  const provider = connector.key as CollectionsProvider;
  const labels = PROVIDER_LABELS[provider] ?? { keyId: "API Key / Key ID", keySecret: "API Secret / Secret Key" };

  const [mode, setMode] = useState<"sandbox" | "production">("sandbox");
  const [keyId, setKeyId] = useState("");
  const [keyIdError, setKeyIdError] = useState<string | undefined>();
  const [keySecret, setKeySecret] = useState("");
  const [keySecretError, setKeySecretError] = useState<string | undefined>();
  const [webhookSecret, setWebhookSecret] = useState("");
  const [clientVersion, setClientVersion] = useState("1");
  const [webhookUsername, setWebhookUsername] = useState("");

  const save = useMutation(
    (input: MerchantAccountIn) => saveMerchantAccount(input),
    {
      onSuccess: (saved) => onConnected(saved),
    },
  );

  function submit() {
    setKeyIdError(undefined);
    setKeySecretError(undefined);

    let ok = true;
    if (!keyId.trim()) {
      setKeyIdError(`Please provide your ${labels.keyId}.`);
      ok = false;
    }
    if (!keySecret.trim()) {
      setKeySecretError(`Please provide your ${labels.keySecret}.`);
      ok = false;
    }

    if (!ok) return;

    const payload: MerchantAccountIn = {
      provider,
      key_id: keyId.trim(),
      key_secret: keySecret.trim(),
      mode,
      ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
      ...(provider === "phonepe"
        ? {
            config: {
              client_version: Number(clientVersion.trim()) || 1,
              webhook_username: webhookUsername.trim(),
            },
          }
        : {}),
    };

    void save.mutate(payload);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Connect ${connector.name}`}
      description={
        connector.description ??
        `Configure your ${connector.name} merchant account. Once saved, payment collections and automatic reconciliation are enabled across all products in your organization.`
      }
      submitLabel="Save & Connect"
      pendingLabel="Saving…"
      pending={save.pending}
    >
      {save.error ? (
        <InlineAlert tone="danger" title="Save failed">
          {save.error.message || "Could not save merchant credentials. Please check the values."}
        </InlineAlert>
      ) : null}

      <RadioGroup
        legend="Environment"
        name="payment-mode"
        value={mode}
        onChange={(val) => setMode(val as "sandbox" | "production")}
        options={[
          { value: "sandbox", label: "Sandbox / Test Mode (Safe for testing)" },
          { value: "production", label: "Production / Live (Processes real payments)" },
        ]}
      />

      <TextField
        label={labels.keyId}
        value={keyId}
        onChange={(e) => setKeyId(e.target.value)}
        placeholder={`Your ${connector.name} ${labels.keyId}`}
        maxLength={200}
        required
        {...(keyIdError ? { error: keyIdError } : {})}
      />

      <TextField
        label={labels.keySecret}
        type="password"
        value={keySecret}
        onChange={(e) => setKeySecret(e.target.value)}
        placeholder="Sealed securely at rest — never revealed again"
        maxLength={500}
        required
        {...(keySecretError ? { error: keySecretError } : {})}
      />

      {provider === "razorpay" || provider === "phonepe" || provider === "stripe" ? (
        <TextField
          label="Webhook secret (optional)"
          type="password"
          value={webhookSecret}
          onChange={(e) => setWebhookSecret(e.target.value)}
          placeholder="Used to cryptographically verify inbound payment notifications"
          maxLength={200}
        />
      ) : null}

      {provider === "phonepe" ? (
        <>
          <TextField
            label="PhonePe client version"
            value={clientVersion}
            onChange={(e) => setClientVersion(e.target.value)}
            placeholder="e.g. 1 or 2"
          />
          <TextField
            label="Webhook username"
            value={webhookUsername}
            onChange={(e) => setWebhookUsername(e.target.value)}
            placeholder="Webhook basic auth username if configured"
          />
        </>
      ) : null}
    </FormDialog>
  );
}
