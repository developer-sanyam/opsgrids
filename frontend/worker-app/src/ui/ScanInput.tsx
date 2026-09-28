import type { KeyboardEvent, Ref } from "react";

import { TextField } from "@opsgrids/ui";

interface ScanInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Fires on Enter — a hardware barcode scanner emulating a keyboard sends one after the code,
   *  the same "keyboard wedge" convention every warehouse scanner on the market uses. A worker
   *  typing by hand gets the same behaviour by pressing Enter themselves. */
  onSubmit?: () => void;
  placeholder?: string;
  helper?: string;
  error?: string;
  ref?: Ref<HTMLInputElement>;
}

/**
 * A barcode/location field built for a physical scanner, not a camera (ADR-0026 §4: no
 * offline-tolerant scan screen this phase, and no camera/ML dependency either — a Bluetooth or
 * USB scanner already emulates a keyboard, typing the code followed by Enter). No `autofocus` —
 * this codebase's lint rules refuse it (`jsx-a11y/no-autofocus`) — so a worker taps the field
 * themselves, same as any other. Large touch target for a phone held with one hand, the same
 * convention the fulfilment plan sets for the worker app generally.
 *
 * Offered as the shared pattern for any later worker-app screen that needs a scan field (F3's
 * pick/pack scan screen, when it lands) — one component, not a second implementation of the same
 * keyboard-wedge handling.
 */
export function ScanInput({
  label,
  value,
  onChange,
  onSubmit,
  placeholder,
  helper,
  error,
  ref,
}: ScanInputProps) {
  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    onSubmit?.();
  }

  return (
    <TextField
      ref={ref}
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={placeholder ?? "Scan, or type and press enter"}
      autoComplete="off"
      autoCorrect="off"
      autoCapitalize="off"
      spellCheck={false}
      inputMode="text"
      className="text-lg"
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
