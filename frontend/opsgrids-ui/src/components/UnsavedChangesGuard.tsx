import { useBlocker } from "react-router-dom";

import { useUnsavedChangesWarning } from "../lib/useUnsavedChangesWarning";
import { ConfirmDialog } from "./ConfirmDialog";

interface UnsavedChangesGuardProps {
  /**
   * The page holds changes that leaving would lose. Compute it from the form's values against
   * what it loaded with.
   *
   * After a successful save, make this `false` *before* navigating away — navigate from an effect
   * on the saved state, not in the same handler as the save, or the guard still sees the old
   * `true` and asks about changes that were just saved.
   */
  when: boolean;
  /** What is being edited, for the question: "the purchase order". Default "this page". */
  subject?: string | undefined;
}

/**
 * The full-page form's half of "confirm before leaving with unsaved changes" (brief §8–9).
 *
 * Leaving has two shapes and this answers both while `when` is true:
 *
 * - **Within the app** — a sidebar link, Back — is held by react-router's blocker and asked in a
 *   `ConfirmDialog`: Stay (focused, and what Escape means) or Leave. Needs the data router every
 *   app already builds with `createBrowserRouter`.
 * - **Out of the app** — reload, close the tab, type a URL — cannot show our dialog, so it gets the
 *   browser's own prompt (`useUnsavedChangesWarning`).
 *
 * A form in a `FormDialog` does not need this: its `dirty` prop covers closing the dialog.
 */
export function UnsavedChangesGuard({ when, subject = "this page" }: UnsavedChangesGuardProps) {
  useUnsavedChangesWarning(when);
  const blocker = useBlocker(when);

  return (
    <ConfirmDialog
      open={blocker.state === "blocked"}
      onClose={() => {
        if (blocker.state === "blocked") blocker.reset();
      }}
      onConfirm={() => {
        if (blocker.state === "blocked") blocker.proceed();
      }}
      title="Leave without saving?"
      body={`Your changes to ${subject} haven't been saved. Leaving now discards them.`}
      confirmLabel="Leave and discard"
      cancelLabel="Stay on page"
    />
  );
}
