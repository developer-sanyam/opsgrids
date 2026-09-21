import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { InlineAlert } from "./InlineAlert";

interface ConfirmDialogProps {
  open: boolean;
  /** Cancel, Escape and the close control. Never called while the confirm is in flight. */
  onClose: () => void;
  /** Fired once per attempt. Guarded here as well as in `useMutation` — see the note below. */
  onConfirm: () => void;
  title: string;
  /**
   * The consequence, in a sentence, before it happens: *what* is being deleted, what it takes
   * with it, and whether it comes back. "Are you sure?" is not a body — the user already knows
   * they clicked something.
   */
  body: ReactNode;
  /** The verb, matching the title: "Delete", "Revoke", "Cancel the order". Never "OK". */
  confirmLabel: string;
  cancelLabel?: string;
  /** What the confirm button says while it works. Defaults to `confirmLabel`. */
  pendingLabel?: string;
  /** `danger` is the default — this is the destructive-action dialog. */
  tone?: "danger" | "primary";
  /** Drive this from `useMutation`'s `pending`. Disables both controls. */
  pending?: boolean;
  /** The server's sentence when the confirm failed. Rendered with a Try again beside it. */
  error?: string;
  /** `problem.request_id`, so a support conversation has a thread to pull. */
  reference?: string;
  /** Anything between the body and the actions — a "this also deletes N items" list. */
  children?: ReactNode;
}

/**
 * The confirmation every destructive action goes through.
 *
 * Three decisions worth stating, because each is a way this component is usually got wrong:
 *
 * **Focus lands on Cancel, never on the destructive button.** A dialog that opens with Delete
 * focused turns a stray Enter — from the keypress that opened it, even — into a deleted row.
 * The safe choice is the one under the fingers.
 *
 * **A confirm fires once per attempt, latched synchronously.** `pending` arrives a render after
 * the click, so disabling the button on it does not stop a double click; the latch does, and it
 * releases when the attempt ends (`pending` falls, or an `error` arrives to try again from).
 *
 * **Closing is refused while it is working.** Escape and Cancel are inert mid-flight: the write
 * is already gone, and a dialog that vanishes leaves the user with no idea whether it happened.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  pendingLabel,
  tone = "danger",
  pending = false,
  error,
  reference,
  children,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const latched = useRef(false);
  const wasPending = useRef(false);

  useEffect(() => {
    if (!open) {
      latched.current = false;
      wasPending.current = false;
    }
  }, [open]);

  useEffect(() => {
    if (pending) {
      wasPending.current = true;
      return;
    }
    if (wasPending.current) {
      wasPending.current = false;
      latched.current = false;
    }
  }, [pending]);

  // A caller that reports the failure without ever setting `pending` still gets a working Try
  // again: a new error means the attempt is over.
  useEffect(() => {
    if (error !== undefined) latched.current = false;
  }, [error]);

  function confirm() {
    if (pending || latched.current) return;
    latched.current = true;
    onConfirm();
  }

  function close() {
    if (pending) return;
    onClose();
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      description={body}
      size="sm"
      initialFocusRef={cancelRef}
      footer={
        <>
          <Button ref={cancelRef} variant="ghost" onClick={close} disabled={pending}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={confirm}
            loading={pending}
          >
            {pending ? (pendingLabel ?? confirmLabel) : confirmLabel}
          </Button>
        </>
      }
    >
      {children !== undefined || error !== undefined ? (
        <div className="flex flex-col gap-4">
          {children}
          {error !== undefined ? (
            <InlineAlert
              tone="danger"
              title={error}
              {...(reference !== undefined ? { reference } : {})}
              action={
                <Button variant="outline" size="sm" onClick={confirm} disabled={pending}>
                  Try again
                </Button>
              }
            />
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}
