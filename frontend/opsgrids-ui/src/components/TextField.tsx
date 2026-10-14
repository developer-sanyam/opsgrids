import type { InputHTMLAttributes, ReactNode, Ref } from "react";

import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

type NativeInput = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "aria-describedby" | "aria-invalid"
>;

interface TextFieldProps extends NativeInput {
  label: string;
  helper?: string | undefined;
  error?: string | undefined;
  optional?: boolean | undefined;
  /**
   * Keep the label in the accessibility tree but off the screen — for a dense row whose column
   * heading already says what the control is. Default false.
   */
  hideLabel?: boolean | undefined;
  containerClassName?: string | undefined;
  /**
   * A control that belongs to the input and sits beside it — a colour swatch, a unit. It shares the
   * input's row, so helper and error text below the field can grow without pulling it out of line
   * with the input (which is what laying it beside the whole field does).
   */
  trailing?: ReactNode | undefined;
  /**
   * The input itself — for moving focus to the field a server error landed on, and for a form
   * dialog's `initialFocusRef`. Without it a screen has to reach its own control by
   * `querySelector`, which is a position in the DOM standing in for an identity.
   */
  ref?: Ref<HTMLInputElement> | undefined;
}

/**
 * A single-line text field. The only input in this system that is not `SearchInput` — which is a
 * pill with a hidden label and a different job entirely.
 */
export function TextField({
  label,
  helper,
  error,
  optional = false,
  hideLabel = false,
  containerClassName,
  trailing,
  className,
  type = "text",
  required,
  ref,
  ...props
}: TextFieldProps) {
  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      optional={optional}
      required={required === true}
      hideLabel={hideLabel}
      {...(containerClassName !== undefined ? { className: containerClassName } : {})}
    >
      {({ id, describedBy, invalid }) => {
        const input = (
          <input
            ref={ref}
            id={id}
            type={type}
            required={required}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            className={fieldSkin(invalid, `h-11 ${className ?? ""}`)}
            {...props}
          />
        );
        return trailing === undefined ? (
          input
        ) : (
          <div className="flex items-center gap-2 [&>input]:min-w-0 [&>input]:flex-1">
            {input}
            {trailing}
          </div>
        );
      }}
    </FormField>
  );
}
