/**
 * This app's mark. Every other glyph comes from the shared set in `@opsgrids/ui`; the mark is the
 * one drawing that is genuinely per-app, and it matches the favicon — a checkmark standing for a
 * confirmed pick/pack, the worker's own action, rather than Operations' warehouse hex.
 */
import type { SVGProps } from "react";

export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <path
      d="M9 16.5 14 21.5 23 11"
      fill="none"
      stroke="#ffffff"
      strokeWidth={2.6}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
