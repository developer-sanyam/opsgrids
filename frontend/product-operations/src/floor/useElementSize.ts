import { useCallback, useState } from "react";

export interface ElementSize {
  width: number;
  height: number;
}

/**
 * The rendered size of an element, kept current as it resizes — `null` until it has been
 * measured, and for good where `ResizeObserver` does not exist (the canvas then draws handles at
 * their millimetre floors). `ref` is a callback ref, so it follows the element through the
 * renders where it is not mounted yet; it must be attached to the element, directly or from
 * another stable callback.
 */
export function useElementSize<T extends Element>() {
  const [size, setSize] = useState<ElementSize | null>(null);

  const ref = useCallback((node: T | null) => {
    if (node === null || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      setSize((previous) =>
        previous !== null && previous.width === box.width && previous.height === box.height
          ? previous
          : { width: box.width, height: box.height },
      );
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return { size, ref };
}
