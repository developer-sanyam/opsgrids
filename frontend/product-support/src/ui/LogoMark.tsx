/**
 * This product's mark. Every other glyph comes from the shared set in `@atmora/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M9 12a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3h-6l-4 3v-3a3 3 0 0 1 0-11Z"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.2}
      strokeLinejoin="round"
    />
    <path
      d="m13.2 14.4 2 2 3.6-3.8"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
