import { useState } from "react";

import { sendCampaignTest } from "@/api/client";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, TextField } from "@/ui";

/**
 * A real copy of the campaign, rendered exactly as recipients would receive it — subject
 * prefixed `[Test] `, same body, a real-looking unsubscribe link that names nobody and
 * suppresses nothing — sent through the organization's own connection with the campaign's sender.
 *
 * Domain verification is **not** required for this, unlike the real send: a test never reaches a
 * real recipient, so it proves the content renders and the connection accepts a message. A
 * connected provider and a sender that has already passed its own test send still are.
 *
 * The caller waits for the provider's answer, the same reasoning `TestSendDialog` (the sender's
 * own test) already states: the answer *is* the outcome, and there is nothing to enqueue until
 * mail has actually left.
 */
export function CampaignTestSendDialog({
  open,
  campaignId,
  campaignName,
  onClose,
  onSent,
}: {
  open: boolean;
  campaignId: string;
  campaignName: string;
  onClose: () => void;
  onSent: (message: string) => void;
}) {
  const [toAddress, setToAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  function close() {
    if (sending) return;
    setToAddress("");
    setError(null);
    setFailure(null);
    onClose();
  }

  async function send() {
    if (sending) return;
    const recipient = toAddress.trim();
    if (recipient === "") {
      setError("Where should the test go? Use a mailbox you can open.");
      setFailure(null);
      return;
    }

    setError(null);
    setSending(true);
    setFailure(null);
    try {
      const result = await sendCampaignTest(campaignId, { to_address: recipient });
      setToAddress("");
      onSent(`Test of “${campaignName}” sent to ${result.to}.`);
    } catch (caught) {
      // The recipient stays in the box: the usual next step after a provider refusal is to fix
      // the connection and press send again, to the same address.
      setFailure(failureMessage(caught));
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Send a preview"
      description={`A real message, exactly as recipients would see “${campaignName}” — same subject (prefixed [Test]), same body, a working but harmless unsubscribe link.`}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={sending}>
            Cancel
          </Button>
          <Button onClick={() => void send()} disabled={sending}>
            {sending ? "Sending…" : "Send test"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="The test didn't go" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <TextField
          label="Send it to"
          type="email"
          value={toAddress}
          onChange={(event) => {
            setToAddress(event.target.value);
            if (error !== null) setError(null);
          }}
          placeholder="you@your-company.com"
          autoComplete="off"
          spellCheck={false}
          helper="A mailbox you can open — nothing here touches your real audience."
          disabled={sending}
          {...(error !== null ? { error } : {})}
        />

        <InlineAlert tone="info" live="off" title="This does not need a verified domain">
          A test proves the content and your connection, not your domain's DNS — it never reaches
          a real recipient.
        </InlineAlert>
      </form>
    </Dialog>
  );
}
