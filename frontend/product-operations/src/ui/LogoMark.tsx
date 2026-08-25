/**
 * This product's mark. Every other glyph comes from the shared set in `@atmora/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M16 7 24 11.2V20.8L16 25 8 20.8V11.2L16 7Z M8 11.2 16 15.4 24 11.2 M16 15.4 V25"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.1}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
