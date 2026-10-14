import { useEffect, useRef, useState } from "react";
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
 * A barcode/location field built for a physical scanner or the camera, not for typing. By default
 * it asks the phone not to raise its on-screen keyboard (`inputMode="none"`): a keyboard covering
 * half the screen while a worker points a scanner at a label is the most common way the field
 * ends up hidden. A keyboard-wedge scanner (Bluetooth or USB, typing the code followed by Enter)
 * still types into the focused field exactly as before. "Type instead" is the way back to a normal
 * keyboard for a label that will not scan — it is always one tap away, never a dead end.
 *
 * No `autofocus` — this codebase's lint rules refuse it (`jsx-a11y/no-autofocus`) — so a worker
 * taps the field themselves, same as any other. Large touch target for a phone held with one hand.
 *
 * The shared pattern for any worker-app screen that needs a scan field — one component, not a
 * second implementation of the same keyboard-wedge handling.
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
  const [typing, setTyping] = useState(false);
  const innerRef = useRef<HTMLInputElement | null>(null);
  const toggled = useRef(false);

  // `inputMode` only takes effect on the next focus, so after the worker taps "Type instead" the
  // field is re-focused: that is what raises the keyboard they just asked for.
  useEffect(() => {
    if (!toggled.current) return;
    toggled.current = false;
    const input = innerRef.current;
    if (!input) return;
    input.blur();
    input.focus();
  }, [typing]);

  function setRefs(node: HTMLInputElement | null) {
    innerRef.current = node;
    if (typeof ref === "function") ref(node);
    else if (ref) ref.current = node;
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    onSubmit?.();
  }

  return (
    <div className="flex flex-col gap-1">
      <TextField
        ref={setRefs}
        label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder ?? "Scan, or type and press enter"}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        inputMode={typing ? "text" : "none"}
        className="text-lg"
        {...(helper !== undefined ? { helper } : {})}
        {...(error !== undefined ? { error } : {})}
      />
      <button
        type="button"
        aria-pressed={typing}
        onClick={() => {
          toggled.current = true;
          setTyping((current) => !current);
        }}
        className="flex min-h-12 w-fit items-center rounded-tile px-2 text-sm font-semibold text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {typing ? "Use scanner" : "Type instead"}
      </button>
    </div>
  );
}
