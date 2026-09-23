import { useEffect, useState } from "react";

/**
 * Holds back a fast-changing value (what someone is typing in the contact search box) so the
 * dependent read fires once typing pauses rather than once per keystroke. Tests pass a small
 * `delayMs` so a keystroke burst still coalesces without spending the window's duration in real
 * wall-clock time.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
