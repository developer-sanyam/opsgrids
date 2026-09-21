/**
 * This product's mark. Every other glyph comes from the shared set in `@opsgrids/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M6.5 10.5h19v13h-19z"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.2}
      strokeLinejoin="round"
    />
    <path
      d="m6.5 11.5 9.5 7 9.5-7"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
