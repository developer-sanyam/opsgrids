import type { InputHTMLAttributes } from "react";

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
