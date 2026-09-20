/**
 * This product's mark. Every other glyph comes from the shared set in `@opsgrids/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M7 12.5h18M9.5 12.5V23h13V12.5M7 12.5 9.8 8h12.4l2.8 4.5"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
