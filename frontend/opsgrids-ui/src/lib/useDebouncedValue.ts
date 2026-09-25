import { useEffect, useState } from "react";

/**
 * Holds back a fast-changing value (what someone is typing) so a dependent effect — a search
 * request — fires once the typing pauses rather than once per keystroke.
 *
 * The one copy. Ten apps carry a byte-for-byte equivalent in their own `src/lib/`; each is
 * deleted in favour of this import as the app is next touched, not in a sweep. Same signature, so
 * the swap is an import line and nothing else.
 *
 * `delayMs` defaults to a real debounce window. Tests pass a small one so a keystroke burst still
 * coalesces without spending the window's duration in wall-clock time.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
