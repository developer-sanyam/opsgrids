import { useEffect, useState } from "react";

/** How long a load may take before the screen says so, per the brief. */
export const SLOW_LOAD_MS = 8000;

/** True once `active` has been true for `afterMs` without interruption; false the moment it ends. */
export function useSlowLoad(active: boolean, afterMs: number = SLOW_LOAD_MS): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), afterMs);
    return () => clearTimeout(timer);
  }, [active, afterMs]);
  return active && slow;
}
