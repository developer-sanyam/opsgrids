import { useEffect, useState } from "react";

/**
 * Holds back a fast-changing value (what someone is typing) so a dependent effect — a search
 * request — fires once the typing pauses rather than once per keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
