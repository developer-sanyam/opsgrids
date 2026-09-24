import type { FormEvent, ReactNode, RefObject } from "react";

import { cn } from "../lib/cn";
import { Button } from "./Button";
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
  /**
   * `danger` for a form whose submit is itself the destructive act — suspending an account,
   * reversing a grant — where the reason is typed into the same dialog that does it. Default
   * `primary`. A plain confirmation with nothing to type is `ConfirmDialog`'s job.
   */
  submitTone?: "primary" | "danger" | undefined;
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
  submitTone = "primary",
  pending = false,
  error,
  reference,
  initialFocusRef,
  size = "md",
  children,
}: FormDialogProps) {
  function close() {
    if (pending) return;
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
            "sticky bottom-0 -mx-5 flex flex-col-reverse gap-2 border-t border-line bg-surface",
            "px-5 py-4 sm:-mx-6 sm:flex-row sm:justify-end sm:px-6",
          )}
        >
          <Button type="button" variant="ghost" onClick={close} disabled={pending}>
            {cancelLabel}
          </Button>
          <Button type="submit" variant={submitTone} loading={pending}>
            {pending ? (pendingLabel ?? submitLabel) : submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
