import type { CSSProperties, ReactNode } from "react";

import type { ThemeTokens } from "../schema";
import { T, tokenStyle } from "./tokens";

/**
 * The one element a theme's tokens are applied to.
 *
 * Everything below reads `var(--st-*)`, so swapping a theme is twelve string writes on this node —
 * no section re-styles and no cascade of bespoke rules to keep in sync. The frame also owns the
 * buyer-facing background and body face, which is why a section never sets either.
 */
export function ThemeFrame({
  tokens,
  children,
  style,
}: {
  tokens: ThemeTokens;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      data-storefront-frame
      style={{
        ...tokenStyle(tokens),
        background: T.bg,
        color: T.text,
        fontFamily: T.fontBody,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
