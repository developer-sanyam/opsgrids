/**
 * The platform's mark: a two-by-two grid with the fourth cell lifted out — the same construction
 * as the marketing site's logo, so the page a customer signs up on and the page they land on
 * after are visibly the same company. Every other glyph comes from `@opsgrids/ui`.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <rect x="7" y="7" width="8" height="8" rx="2" fill="#ffffff" opacity="0.92" />
    <rect x="17" y="7" width="8" height="8" rx="2" fill="#ffffff" opacity="0.92" />
    <rect x="7" y="17" width="8" height="8" rx="2" fill="#ffffff" opacity="0.92" />
    <rect x="18.5" y="15.5" width="8" height="8" rx="2" fill="#ffffff" />
  </svg>
);
