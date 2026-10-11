import { useMemo } from "react";

import { code128Bars } from "@/lib/code128";

/** Modules of white either side. Ten is the standard's minimum for a reliable linear read. */
const QUIET_ZONE = 10;

interface Barcode128Props {
  /** The exact text to encode — printable ASCII. */
  value: string;
  /** What the code is, for anyone who cannot scan it. */
  label: string;
  /** Rendered height in pixels; the width follows the symbol. */
  height?: number;
  className?: string;
}

/**
 * A scannable Code 128 bar code as SVG.
 *
 * Always black on white in both themes, for the reason `QrCode` gives: a camera or a laser has to
 * read it, and a theme-tinted bar code that reads on a developer's screen and fails on a
 * handheld is the bug that never reproduces.
 */
export function Barcode128({ value, label, height = 56, className }: Barcode128Props) {
  const { bars, extent } = useMemo(() => {
    const { bars: runs, modules } = code128Bars(value);
    return { bars: runs, extent: modules + QUIET_ZONE * 2 };
  }, [value]);

  return (
    <svg
      viewBox={`0 0 ${extent} 1`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      className={className}
      data-modules={extent}
    >
      <rect width={extent} height={1} fill="white" />
      {bars.map(([x, w]) => (
        <rect key={x} x={x + QUIET_ZONE} width={w} height={1} fill="black" />
      ))}
    </svg>
  );
}
