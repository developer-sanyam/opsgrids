import { useEffect, useState } from "react";

/**
 * Holds back a fast-changing value (what someone is typing) so a dependent effect — a search
 * request — fires once the typing pauses rather than once per keystroke. `delayMs` defaults to a
 * real debounce window; tests pass a small one so a keystroke burst still coalesces without
 * spending the window's duration in real wall-clock time.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
