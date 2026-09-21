import type { InputHTMLAttributes, Ref } from "react";

import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

type NativeInput = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "aria-describedby" | "aria-invalid"
>;

interface TextFieldProps extends NativeInput {
  label: string;
  helper?: string;
  error?: string;
  optional?: boolean;
  containerClassName?: string;
  /**
   * The input itself — for moving focus to the field a server error landed on, and for a form
   * dialog's `initialFocusRef`. Without it a screen has to reach its own control by
   * `querySelector`, which is a position in the DOM standing in for an identity.
   */
  ref?: Ref<HTMLInputElement>;
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
  containerClassName,
  className,
  type = "text",
  ref,
  ...props
}: TextFieldProps) {
  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      optional={optional}
      {...(containerClassName !== undefined ? { className: containerClassName } : {})}
    >
      {({ id, describedBy, invalid }) => (
        <input
          ref={ref}
          id={id}
          type={type}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={fieldSkin(invalid, `h-11 ${className ?? ""}`)}
          {...props}
        />
      )}
    </FormField>
  );
}
