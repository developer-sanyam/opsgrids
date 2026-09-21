import { useState } from "react";

import { sendTestMessage } from "@/api/client";
import type { SenderOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, TextField } from "@/ui";

/**
 * The gate, as a screen: send a real message through the organization's own connection and, only
 * if it arrives, the sender becomes usable.
 *
 * The subject and the body are the service's and are not offered here. A customisable test
 * message is a send nobody reviewed, going to an address of the caller's choosing.
 *
 * The caller waits for the provider's answer because that answer *is* the state change — there is
 * nothing to enqueue until mail has actually left. A failure carries the provider's own words
 * (`535 5.7.8 Username and Password not accepted`), which is the one sentence that says which of
 * the fourteen things they typed is wrong.
 */
export function TestSendDialog({
  sender,
  onClose,
  onSent,
}: {
  /** The sender being tested, or `null` when no dialog is up. */
  sender: SenderOut | null;
  onClose: () => void;
  onSent: (sender: SenderOut, message: string) => void;
}) {
  const [toAddress, setToAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  function close() {
    setToAddress("");
    setError(null);
    setFailure(null);
    onClose();
  }

  async function send() {
    if (sending || sender === null) return;
    const recipient = toAddress.trim();
    if (recipient === "") {
      setError("Where should the test message go? Use a mailbox you can open.");
      setFailure(null);
      return;
    }

    setError(null);
    setSending(true);
    setFailure(null);
    try {
      const tested = await sendTestMessage(sender.id, { to_address: recipient });
      setToAddress("");
      onSent(
        tested,
        `Test message delivered. ${tested.address} can now be used to send.`,
      );
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
      open={sender !== null}
      onClose={close}
      title="Send a test message"
      description={
        sender === null
          ? ""
          : `Engage sends a fixed message as ${sender.address}. It becomes usable only if your provider accepts it.`
      }
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
          <InlineAlert tone="danger" title="The test message didn't go" className="mb-4">
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
          helper="A mailbox you can open — the message arriving is the proof."
          disabled={sending}
          {...(error !== null ? { error } : {})}
        />

        <InlineAlert tone="info" live="off" title="This is a real message">
          It goes out through your provider and counts against its daily limit, like any other
          send.
        </InlineAlert>
      </form>
    </Dialog>
  );
}
