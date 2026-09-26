import type { Ref, SelectHTMLAttributes } from "react";

import { ChevronDown } from "../icons";
import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean | undefined;
}

type NativeSelect = Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "id" | "aria-describedby" | "aria-invalid" | "children"
>;

interface SelectProps extends NativeSelect {
  label: string;
  options: SelectOption[];
  helper?: string | undefined;
  error?: string | undefined;
  /**
   * Keep the label in the accessibility tree but off the screen — for a dense row whose column
   * heading already says what the control is. Default false.
   */
  hideLabel?: boolean | undefined;
  /** The unselected first entry. Omit it where a value is always chosen. */
  placeholder?: string | undefined;
  containerClassName?: string | undefined;
  /** The select itself — for moving focus to the field a server error landed on. */
  ref?: Ref<HTMLSelectElement> | undefined;
}

/**
 * A native `<select>`, styled.
 *
 * Native on purpose: it is keyboard- and screen-reader-complete in every browser, it opens as the
 * platform's own control on a phone, and a hand-rolled listbox buys nothing here. Where the
 * options are few and all worth seeing at once, use `RadioGroup` instead.
 */
export function Select({
  label,
  options,
  helper,
  error,
  hideLabel = false,
  placeholder,
  containerClassName,
  className,
  required,
  ref,
  ...props
}: SelectProps) {
  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      required={required === true}
      hideLabel={hideLabel}
      {...(containerClassName !== undefined ? { className: containerClassName } : {})}
    >
      {({ id, describedBy, invalid }) => (
        <div className="relative">
          <select
            ref={ref}
            id={id}
            required={required}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            className={fieldSkin(invalid, `h-11 cursor-pointer appearance-none pr-9 ${className ?? ""}`)}
            {...props}
          >
            {placeholder !== undefined ? (
              <option value="" disabled>
                {placeholder}
              </option>
            ) : null}
            {options.map((option) => (
              <option key={option.value} value={option.value} disabled={option.disabled ?? false}>
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-faint"
            aria-hidden
          />
        </div>
      )}
    </FormField>
  );
}
