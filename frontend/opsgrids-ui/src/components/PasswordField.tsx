import { useState } from "react";
import type { InputHTMLAttributes } from "react";

import { EyeIcon, EyeOffIcon } from "../icons";
import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

type NativeInput = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "type" | "aria-describedby" | "aria-invalid" | "value" | "onChange"
>;

interface PasswordFieldProps extends NativeInput {
  label?: string | undefined;
  value: string;
  onChange: (value: string) => void;
  helper?: string | undefined;
  error?: string | undefined;
  /** `current-password` to sign in, `new-password` to choose one. */
  autoComplete?: "current-password" | "new-password" | undefined;
}

/**
 * A password input with a show/hide control inside its right edge. The toggle is a 44px target,
 * says what it will do ("Show password"), and reports its state with `aria-pressed`; the value
 * is never lost by toggling. Built on `FormField`, so the label, helper and error are tied to the
 * input like every other field.
 */
export function PasswordField({
  label = "Password",
  value,
  onChange,
  helper,
  error,
  autoComplete = "current-password",
  required = true,
  className,
  ...props
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);
  return (
    <FormField
      label={label}
      required={required}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
    >
      {({ id, describedBy, invalid }) => (
        <div className="relative">
          <input
            id={id}
            type={visible ? "text" : "password"}
            autoComplete={autoComplete}
            required={required}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            className={fieldSkin(invalid, `h-11 pr-12 ${className ?? ""}`)}
            {...props}
          />
          <button
            type="button"
            onClick={() => setVisible((current) => !current)}
            aria-label={visible ? "Hide password" : "Show password"}
            aria-pressed={visible}
            className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-tile text-muted transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-focus"
          >
            {visible ? <EyeOffIcon className="size-5" /> : <EyeIcon className="size-5" />}
          </button>
        </div>
      )}
    </FormField>
  );
}
