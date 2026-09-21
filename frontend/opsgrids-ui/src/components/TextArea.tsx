import type { Ref, TextareaHTMLAttributes } from "react";

import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

type NativeTextArea = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  "id" | "aria-describedby" | "aria-invalid"
>;

interface TextAreaProps extends NativeTextArea {
  label: string;
  helper?: string | undefined;
  error?: string | undefined;
  optional?: boolean | undefined;
  /**
   * Show `{n}/{maxLength}` once the value reaches this length. A counter that is on from the
   * first character reads as a limit to obey; one that appears near the end reads as a warning,
   * which is what it is. Requires `maxLength` and `value`.
   */
  counterFrom?: number | undefined;
  /**
   * Keep the label in the accessibility tree but off the screen — for a dense row whose column
   * heading already says what the control is. Default false.
   */
  hideLabel?: boolean | undefined;
  containerClassName?: string | undefined;
  /** The textarea itself — for moving focus to the field a server error landed on. */
  ref?: Ref<HTMLTextAreaElement> | undefined;
}

/**
 * The multi-line field: a requester's reason, a decider's comment.
 *
 * Its validation mirrors the server's exactly and invents nothing of its own — a minimum length
 * only one client enforces drifts the moment a second client exists.
 */
export function TextArea({
  label,
  helper,
  error,
  optional = false,
  hideLabel = false,
  counterFrom,
  containerClassName,
  className,
  rows = 4,
  maxLength,
  value,
  ref,
  ...props
}: TextAreaProps) {
  const length = typeof value === "string" ? value.length : 0;
  const showCounter =
    counterFrom !== undefined && maxLength !== undefined && length >= counterFrom;

  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      optional={optional}
      hideLabel={hideLabel}
      {...(containerClassName !== undefined ? { className: containerClassName } : {})}
      {...(showCounter
        ? {
            labelAside: (
              <span
                className="text-xs tnum text-muted"
                // The count is described by the field itself; announcing every keystroke would
                // make the field unusable with a screen reader.
                aria-hidden
              >
                {length}/{maxLength}
              </span>
            ),
          }
        : {})}
    >
      {({ id, describedBy, invalid }) => (
        <textarea
          ref={ref}
          id={id}
          rows={rows}
          value={value}
          {...(maxLength !== undefined ? { maxLength } : {})}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={fieldSkin(invalid, `py-2 leading-relaxed ${className ?? ""}`)}
          {...props}
        />
      )}
    </FormField>
  );
}
