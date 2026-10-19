import { useState } from "react";

import { useMutation } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, StatusBadge } from "@opsgrids/ui";

import { deleteMerchantAccount, verifyMerchantAccount } from "@/api/integrations";
import type { MerchantAccountOut } from "@/api/integrations";

interface ManagePaymentDialogProps {
  account: MerchantAccountOut;
  onClose: () => void;
  onUpdated: (account: MerchantAccountOut) => void;
  onDisconnected: () => void;
}

export function ManagePaymentDialog({
  account,
  onClose,
  onUpdated,
  onDisconnected,
}: ManagePaymentDialogProps) {
  const [feedback, setFeedback] = useState<{ tone: "success" | "danger"; message: string } | null>(null);

  const verify = useMutation(
    () => verifyMerchantAccount(),
    {
      onSuccess: (updated) => {
        setFeedback({
          tone: "success",
          message: "Merchant account credentials verified successfully with payment provider.",
        });
        onUpdated(updated);
      },
      onError: (err) => {
        setFeedback({
          tone: "danger",
          message: `Verification check failed: ${err.message}`,
        });
      },
    },
  );

  const disconnect = useMutation(
    () => deleteMerchantAccount("Disconnected by administrator from Integrations hub"),
    {
      onSuccess: () => {
        onDisconnected();
        onClose();
      },
      onError: (err) => {
        setFeedback({
          tone: "danger",
          message: `Disconnect failed: ${err.message}`,
        });
      },
    },
  );

  return (
    <Dialog open onClose={onClose} title={`Manage ${account.provider.toUpperCase()} Integration`}>
      <div className="flex flex-col gap-6">
        {feedback ? (
          <InlineAlert tone={feedback.tone} title={feedback.tone === "success" ? "Success" : "Error"}>
            {feedback.message}
          </InlineAlert>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-card bg-surface-muted p-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Provider</span>
            <span className="text-base font-bold capitalize">{account.provider}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Mode</span>
            <span className="text-sm font-medium capitalize">{account.mode}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Key ID / App ID</span>
            <span className="tnum font-mono text-xs">{account.key_id}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Status</span>
            <StatusBadge
              tone={account.verified ? "success" : "warning"}
              label={account.verified ? "Verified" : "Unverified"}
            />
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <h4 className="text-sm font-bold uppercase tracking-wider text-muted">Provider Health & Diagnostics</h4>
          <p className="text-sm text-muted">
            Test the live API connection to {account.provider}. This verifies that your API credentials have not expired and can sign customer transactions.
          </p>
          <div>
            <Button
              variant="outline"
              disabled={verify.pending}
              onClick={() => void verify.mutate(undefined)}
            >
              {verify.pending ? "Verifying with provider…" : "Verify Credentials Now"}
            </Button>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-danger-line pt-4">
          <div>
            <p className="text-sm font-bold text-danger">Disconnect Payment Gateway</p>
            <p className="text-xs text-muted">Stops customer card/UPI checkouts and automated webhook reconciliation.</p>
          </div>
          <Button
            size="sm"
            variant="danger"
            disabled={disconnect.pending}
            onClick={() => void disconnect.mutate(undefined)}
          >
            {disconnect.pending ? "Disconnecting…" : "Disconnect"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
