import { useEffect, useState } from "react";

/**
 * Holds `value`, updated `delayMs` after it stops changing. Used to keep a search box from
 * refetching the queue on every keystroke — each keystroke otherwise drops the table to
 * skeletons while the previous request is still in flight.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);

  return debounced;
}
