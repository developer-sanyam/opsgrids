import { useMemo } from "react";

import { cn } from "../lib/cn";
import { encodeQr, qrPathData } from "../lib/qr";
import type { QrLevel } from "../lib/qr";

interface QrCodeProps {
  /** The exact string to encode. Encoded verbatim — never trimmed, normalised or re-cased. */
  value: string;
  /** Error-correction level. Higher survives more damage and costs modules; default `"M"`. */
  level?: QrLevel;
  /** Rendered width and height in pixels. The SVG scales, so this is a hint, not a raster size. */
  size?: number;
  /**
   * What the code is, for anyone who cannot scan it. Required: a code with no accessible name is
   * an image of nothing to a screen reader, and this one usually carries the only copy of a legal
   * identifier on the page.
   */
  label: string;
  className?: string;
}

/** Modules of white margin around the code. Four is the standard's minimum for a reliable read. */
const QUIET_ZONE = 4;

/**
 * A scannable QR Code, rendered as SVG.
 *
 * **Always dark-on-white, in both themes.** A QR Code is not a picture, it is a thing a camera
 * has to read, and while some scanners cope with an inverted code many do not. A dark-mode-tinted
 * code that reads on the developer's phone and fails on the warehouse's handheld is exactly the
 * bug that never reproduces — so the palette here is deliberately not themed. The frame around it
 * is the caller's to theme.
 */
export function QrCode({ value, level = "M", size = 200, label, className }: QrCodeProps) {
  const { path, extent, version } = useMemo(() => {
    const matrix = encodeQr(value, { level });
    return {
      path: qrPathData(matrix, QUIET_ZONE),
      extent: matrix.size + QUIET_ZONE * 2,
      version: matrix.version,
    };
  }, [value, level]);

  return (
    <svg
      viewBox={`0 0 ${extent} ${extent}`}
      width={size}
      height={size}
      role="img"
      aria-label={label}
      // Module edges land on exact integers; antialiasing them softens the contrast a scanner
      // depends on at small sizes.
      shapeRendering="crispEdges"
      className={cn("block", className)}
      data-qr-version={version}
    >
      <rect width={extent} height={extent} fill="#ffffff" />
      <path d={path} fill="#0b0b11" />
    </svg>
  );
}
