import { useEffect, useState } from "react";
import type { FormEvent, ReactNode, RefObject } from "react";

import { cn } from "../lib/cn";
import { useUnsavedChangesWarning } from "../lib/useUnsavedChangesWarning";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { Dialog } from "./Dialog";
import { InlineAlert } from "./InlineAlert";

interface FormDialogProps {
  open: boolean;
  /** Cancel, Escape and the close control. Inert while the submit is in flight. */
  onClose: () => void;
  /** The submit handler. Already has `preventDefault` called for it. */
  onSubmit: () => void;
  title: string;
  /** One sentence of context where the title needs it. Tied by `aria-describedby`. */
  description?: ReactNode | undefined;
  /** The verb: "Create widget", "Save changes". Never "Submit" or "OK". */
  submitLabel: string;
  cancelLabel?: string | undefined;
  /** What the submit button says while it works — "Saving…". */
  pendingLabel?: string | undefined;
  /** From `useMutation`'s `pending`. */
  pending?: boolean | undefined;
  /**
   * The failure that belongs to no single field — a 409, a 403, a 500. Field-level messages go
   * on the fields through `fieldErrors`, never up here.
   */
  error?: string | undefined;
  /** `problem.request_id`. */
  reference?: string | undefined;
  /** The first control worth answering. Without it focus lands on the dialog's heading. */
  initialFocusRef?: RefObject<HTMLElement | null> | undefined;
  size?: "sm" | "md" | "lg" | undefined;
  /**
   * The fields hold something closing would throw away. Opt-in: while it is true, Cancel,
   * Escape and the close control ask "Discard unsaved changes?" first, and reloading or closing
   * the tab asks the browser's own question. Compute it from the fields — typed text differs
   * from what the form opened with — not from "a key was pressed", or a user who typed and
   * deleted is asked to discard nothing.
   */
  dirty?: boolean | undefined;
  /**
   * The submit does something destructive or irreversible - suspend, delete, remove. Draws the
   * submit button as `destructive` (semantic red) instead of the accent. The label still says what
   * it does; colour never carries that alone.
   */
  destructive?: boolean | undefined;
  /** The fields. See the note on resetting, below. */
  children: ReactNode;
}

/**
 * A `Dialog` hosting a real `<form>`: Enter submits, the footer sticks, and the whole thing is
 * one control a keyboard can finish without reaching for the mouse.
 *
 * **The footer lives inside the form**, rather than in `Dialog`'s `footer` slot, so the submit
 * button is the form's own — implicit submission on Enter is then native browser behaviour and
 * not a keydown handler pretending to be it. It sticks to the bottom of the scrolling body, so a
 * twenty-field form still has Save in reach.
 *
 * **`noValidate`**: the browser's own bubbles cannot be styled, do not survive a scroll, and say
 * things like "Please match the requested format". Validate in the submit handler and put the
 * message on the field through `FormField`'s `error`, where it is tied to the control by
 * `aria-describedby` and read when focus arrives.
 *
 * **Resetting is structural, not manual.** The children are unmounted when `open` is false, so
 * keep the fields' `useState` in a component rendered *inside* this dialog — a `<WidgetFields>` —
 * and closing resets the form by unmounting it. State hoisted into the screen survives, and the
 * next Create opens on the last edit's leftovers.
 *
 * **Unsaved changes** (`dirty`) are confirmed before they are discarded, in a `ConfirmDialog`
 * stacked over this one: Escape there means "keep editing" and returns to the form, and only
 * "Discard changes" closes both. A successful save is not a discard — the screen closes the
 * dialog through its own `open`, which never asks.
 */
export function FormDialog({
  open,
  onClose,
  onSubmit,
  title,
  description,
  submitLabel,
  cancelLabel = "Cancel",
  pendingLabel,
  pending = false,
  error,
  reference,
  initialFocusRef,
  size = "md",
  dirty = false,
  destructive = false,
  children,
}: FormDialogProps) {
  const [confirming, setConfirming] = useState(false);
  useUnsavedChangesWarning(open && dirty);

  // Closed from outside while the question was up: the next open starts on the form, not on it.
  useEffect(() => {
    if (!open) setConfirming(false);
  }, [open]);

  function close() {
    if (pending) return;
    if (dirty) {
      setConfirming(true);
      return;
    }
    onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // React bubbles synthetic events through the component tree, not the DOM tree — a dialog
    // portalled to `document.body` is still, as far as React is concerned, nested inside
    // whatever form opened it. Without this, submitting a dialog opened from inside another
    // dialog's form also fires the outer form's `onSubmit`, which then validates fields the
    // user never touched.
    event.stopPropagation();
    if (pending) return;
    onSubmit();
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      {...(description !== undefined ? { description } : {})}
      {...(initialFocusRef !== undefined ? { initialFocusRef } : {})}
      size={size}
    >
      <form noValidate onSubmit={submit} className="flex flex-col gap-5">
        <div className="flex flex-col gap-4">{children}</div>

        {error !== undefined ? (
          <InlineAlert
            tone="danger"
            title={error}
            {...(reference !== undefined ? { reference } : {})}
          />
        ) : null}

        <div
          className={cn(
            "sticky bottom-0 -mx-5 flex flex-row justify-end gap-2 border-t border-line bg-surface",
            "px-5 py-4 sm:-mx-6 sm:px-6 flex-wrap max-sm:[&>*]:grow",
          )}
        >
          <Button type="button" variant="ghost" onClick={close} disabled={pending}>
            {cancelLabel}
          </Button>
          <Button type="submit" variant={destructive ? "destructive" : "primary"} loading={pending}>
            {pending ? (pendingLabel ?? submitLabel) : submitLabel}
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={open && confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false);
          onClose();
        }}
        title="Discard unsaved changes?"
        body={`What you've entered in “${title}” hasn't been saved and will be lost.`}
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
      />
    </Dialog>
  );
}
