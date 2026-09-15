/**
 * This product's mark. Every other glyph comes from the shared set in `@opsgrids/ui`; the mark is
 * the one drawing that is genuinely per-product, and it matches the favicon.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <rect x="8" y="9" width="13" height="3.2" rx="1.6" fill="#ffffff" />
    <rect x="8" y="14.4" width="9" height="3.2" rx="1.6" fill="#ffffff" opacity="0.85" />
    <rect x="8" y="19.8" width="16" height="3.2" rx="1.6" fill="#ffffff" opacity="0.65" />
  </svg>
);
