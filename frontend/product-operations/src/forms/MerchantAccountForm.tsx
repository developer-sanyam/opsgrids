import { useState } from "react";

import type { CollectionsProvider, MerchantAccountIn } from "@/api/types";
import { PROVIDER_NAME } from "@/lib/paymentProviders";
import { Button, InlineAlert, RadioGroup, TextField } from "@/ui";

interface MerchantAccountFormProps {
  provider: CollectionsProvider;
  /** The key id / App ID already connected, when replacing credentials. */
  initialKeyId: string;
  /** A connected Cashfree account's environment is fixed: switching is disconnect, then connect. */
  fixedMode: "sandbox" | "production" | null;
  liveAllowed: boolean;
  pending: boolean;
  error: string | null;
  onSubmit: (input: MerchantAccountIn) => void;
  onCancel: () => void;
}

/**
 * The credentials of one provider account, typed once and sent straight to the payment service,
 * which seals them; the parent clears this form the moment the save lands, so a secret never
 * outlives the request in the page's memory. Razorpay takes a key id, a key secret and the
 * webhook secret the merchant set in its dashboard; Cashfree an App ID, a Secret Key (which also
 * signs its webhooks, so there is no separate webhook secret) and the environment — an App ID does
 * not say whether it is Sandbox or Production.
 */
export function MerchantAccountForm({
  provider,
  initialKeyId,
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
  const [mode, setMode] = useState<"sandbox" | "production">(fixedMode ?? "sandbox");
  const cashfree = provider === "cashfree";

  function submit() {
    if (cashfree) {
      onSubmit({ provider: "cashfree", key_id: keyId.trim(), key_secret: keySecret, mode });
    } else {
      onSubmit({ provider: "razorpay", key_id: keyId.trim(), key_secret: keySecret, webhook_secret: webhookSecret });
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      aria-label={`${PROVIDER_NAME[provider]} credentials`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {error ? <InlineAlert tone="danger" title={error} /> : null}
      <TextField
        label={cashfree ? "App ID" : "Key id"}
        value={keyId}
        onChange={(e) => setKeyId(e.target.value)}
        autoComplete="off"
        required
      />
      <TextField
        label={cashfree ? "Secret Key" : "Key secret"}
        type="password"
        value={keySecret}
        onChange={(e) => setKeySecret(e.target.value)}
        autoComplete="new-password"
        required
      />
      {cashfree ? (
        <RadioGroup
          legend="Environment"
          name="cashfree-environment"
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
              description: liveAllowed
                ? "Real payments, into your Cashfree account."
                : "Not enabled on this platform yet.",
              disabled: !liveAllowed || (fixedMode !== null && fixedMode !== "production"),
            },
          ]}
        />
      ) : (
        <TextField
          label="Webhook secret"
          helper="The secret you set on the webhook in Razorpay's dashboard."
          type="password"
          value={webhookSecret}
          onChange={(e) => setWebhookSecret(e.target.value)}
          autoComplete="new-password"
          required
        />
      )}
      <div className="flex gap-2">
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
