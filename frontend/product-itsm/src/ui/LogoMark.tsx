/**
 * This product's mark. Every other glyph comes from the shared set in `@atmora/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M16 6.5 24 9.5v5.2c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V9.5z"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.1}
      strokeLinejoin="round"
    />
    <path
      d="m12.4 15.4 2.6 2.6 4.6-5"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
