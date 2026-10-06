import { useEffect, useState } from "react";

const QUERY = "(any-pointer: coarse)";

/**
 * Whether anything the device offers points coarsely — a finger or a pen. The canvas gives its
 * handles and nodes a touch-sized target only then, so a mouse is not met by invisible targets
 * wider than the shapes they sit on. `false` where there is no `matchMedia`.
 */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia(QUERY);
    setCoarse(query.matches);
    const onChange = (event: MediaQueryListEvent) => setCoarse(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return coarse;
}
