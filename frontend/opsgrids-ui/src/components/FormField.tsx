import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { FIELD_LABEL } from "../lib/field";

/** The ids a control must carry for its label, helper and error to reach a screen reader. */
export interface FieldIds {
  id: string;
  /** Pass straight to `aria-describedby`. Already joins the helper and the error. */
  describedBy: string | undefined;
  invalid: boolean;
  /**
   * The field is marked required. A control built on `FormField` directly puts this on
   * `aria-required` (the wrappers carry the native `required` attribute instead), so a screen
   * reader says "required" — the asterisk beside the label is for the eye only.
   */
  required: boolean;
}

interface FormFieldProps {
  label: string;
  /**
   * The example or the rule. Stays on screen when an error appears — an error **appends**, it
   * never replaces the helper, because the user still needs to know what a valid value looks like.
   */
  helper?: ReactNode | undefined;
  /** The server's message, verbatim (§2.5). Never a rewritten, friendlier version of it. */
  error?: string | undefined;
  /** Renders `(optional)` beside the label. */
  optional?: boolean | undefined;
  /**
   * A red asterisk after the label (the redesign's convention, owner ruling 2026-09-25). Hidden
   * from assistive technology, which hears "required" from the control's own state instead —
   * see `FieldIds.required`. `TextField`, `TextArea` and `Select` set it for a `required`
   * control inside a `FormSection`; outside one, labels are as they were.
   */
  required?: boolean | undefined;
  /**
   * Keep the `<label>` — real, tied to the control, read by a screen reader — but take it off the
   * screen. For a dense row where the column heading above already says what the control is: the
   * per-row scope `Select` in a permission picker, an inline quantity on a line item. The visible
   * heading is not a label; it is not tied to anything and a screen reader announcing "combo box"
   * with no name is the result. Off by default, and a control that is alone on its row keeps its
   * visible label.
   */
  hideLabel?: boolean | undefined;
  /** Right of the label — a character counter, a unit toggle. */
  labelAside?: ReactNode | undefined;
  className?: string | undefined;
  children: (ids: FieldIds) => ReactNode;
}

/**
 * Label, control, helper, error — wired together once so no screen has to remember
 * `aria-describedby` again.
 *
 * The field error is described rather than announced: it is tied to the control by
 * `aria-describedby` and flagged by `aria-invalid`, so it is read when focus arrives. A
 * `role="alert"` per field would interrupt five times on a five-error submit; the form's summary
 * is the one thing that announces (§2.5).
 */
export function FormField({
  label,
  helper,
  error,
  optional = false,
  required = false,
  hideLabel = false,
  labelAside,
  className,
  children,
}: FormFieldProps) {
  const base = useId();
  const id = `${base}-control`;
  const helperId = helper ? `${base}-helper` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const describedBy = [helperId, errorId].filter(Boolean).join(" ") || undefined;

  const labelNode = (
    <label
      htmlFor={id}
      className={hideLabel ? "sr-only" : FIELD_LABEL}
    >
      {label}
      {optional ? (
        // A literal space, not just the `ml-1` margin: the accessible name is computed from
        // text content, so a margin-only gap is invisible to it and "Comment(optional)" is
        // what a screen reader reads without this.
        <>
          {" "}
          <span className="normal-case text-faint">(optional)</span>
        </>
      ) : null}
    </label>
  );

  // Outside the `<label>`, so the label's text — the control's accessible name, and what every
  // `getByLabelText` in the apps' tests matches — is the same with or without the mark.
  const marked =
    required && !hideLabel ? (
      <span className="inline-flex items-baseline gap-1">
        {labelNode}
        <span aria-hidden className="text-body-sm font-bold leading-none text-negative">
          *
        </span>
      </span>
    ) : (
      labelNode
    );

  return (
    <div data-field className={cn("flex flex-col gap-1.5", className)}>
      {/*
        A hidden label with nothing beside it is rendered bare rather than inside the label row.
        `sr-only` takes it out of flow, so it is not a flex item and the column's `gap` does not
        reserve a row for it — which is the whole point of hiding it in a dense table.
      */}
      {hideLabel && labelAside === undefined ? (
        labelNode
      ) : (
        <div className="flex items-baseline justify-between gap-3">
          {marked}
          {labelAside}
        </div>
      )}
      {children({ id, describedBy, invalid: error !== undefined, required })}
      {helper || error ? (
        // One element however many messages there are, so a field is always label / control /
        // messages: three rows a `FIELD_GRID` can line up across a row of fields.
        <div className="flex flex-col gap-1.5">
          {helper ? (
            <p id={helperId} className="text-xs text-muted">
              {helper}
            </p>
          ) : null}
          {error ? (
            <p id={errorId} className="text-xs font-medium text-negative">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
