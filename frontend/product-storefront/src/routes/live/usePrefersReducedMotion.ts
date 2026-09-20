import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Whether this visitor has asked for less motion.
 *
 * The console's stylesheet already switches off its own animations under this query; this hook is
 * for the one place that animates from JavaScript — the preview control, which slides out of the
 * way as a buyer scrolls. Under `reduce` it still gets out of the way, it just stops sliding:
 * removing the *movement*, not the behaviour, is what the preference asks for.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(QUERY).matches === true,
  );

  useEffect(() => {
    const media = window.matchMedia?.(QUERY);
    if (!media) return;
    const onChange = () => setReduced(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
