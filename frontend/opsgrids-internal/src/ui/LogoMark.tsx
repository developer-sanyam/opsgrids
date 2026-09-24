/**
 * The operator console's mark — a violet square with a control-sliders glyph, matching the favicon.
 * Every other glyph comes from the shared set in `@opsgrids/ui`; the mark is the one drawing that
 * is genuinely per-app.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <g fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" className="text-white">
      <path d="M8 11h16M8 16h16M8 21h16" />
      <circle cx="13" cy="11" r="2.4" fill="var(--color-accent)" />
      <circle cx="20" cy="16" r="2.4" fill="var(--color-accent)" />
      <circle cx="12" cy="21" r="2.4" fill="var(--color-accent)" />
    </g>
  </svg>
);
