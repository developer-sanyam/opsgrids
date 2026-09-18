/**
 * Theme tokens become CSS custom properties on one wrapper element.
 *
 * Every section below that wrapper styles itself from `var(--st-*)` and nothing else, so changing
 * a theme is a token swap on a single node — no section re-styles, no per-section palette, and the
 * editor's live preview updates by writing twelve strings.
 */

import type { CSSProperties } from "react";

import type { ThemeTokens } from "../schema";

/** The custom-property names a section may read. Renaming one is a breaking change. */
export const TOKEN_VARS = {
  colorBg: "--st-bg",
  colorSurface: "--st-surface",
  colorText: "--st-text",
  colorMuted: "--st-muted",
  colorAccent: "--st-accent",
  colorAccentText: "--st-accent-text",
  colorBorder: "--st-border",
  fontHeading: "--st-font-heading",
  fontBody: "--st-font-body",
  radius: "--st-radius",
  space: "--st-space",
  maxWidth: "--st-max-width",
} as const satisfies Record<keyof ThemeTokens, string>;

/**
 * The style object for the theme wrapper. Cast at the boundary because React's `CSSProperties`
 * does not model custom properties; every value going in is already typed by `ThemeTokens`.
 */
export function tokenStyle(tokens: ThemeTokens): CSSProperties {
  const vars: Record<string, string> = {
    [TOKEN_VARS.colorBg]: tokens.colorBg,
    [TOKEN_VARS.colorSurface]: tokens.colorSurface,
    [TOKEN_VARS.colorText]: tokens.colorText,
    [TOKEN_VARS.colorMuted]: tokens.colorMuted,
    [TOKEN_VARS.colorAccent]: tokens.colorAccent,
    [TOKEN_VARS.colorAccentText]: tokens.colorAccentText,
    [TOKEN_VARS.colorBorder]: tokens.colorBorder,
    [TOKEN_VARS.fontHeading]: tokens.fontHeading,
    [TOKEN_VARS.fontBody]: tokens.fontBody,
    [TOKEN_VARS.radius]: `${tokens.radius}px`,
    [TOKEN_VARS.space]: `${tokens.space}px`,
    [TOKEN_VARS.maxWidth]: `${tokens.maxWidth}px`,
  };
  return vars as CSSProperties;
}

/* Shorthands the section components read. Written once here so no section ever spells a token
   name itself — a typo'd `var()` fails silently, which is exactly the bug this prevents. */
export const T = {
  bg: "var(--st-bg)",
  surface: "var(--st-surface)",
  text: "var(--st-text)",
  muted: "var(--st-muted)",
  accent: "var(--st-accent)",
  accentText: "var(--st-accent-text)",
  border: "var(--st-border)",
  fontHeading: "var(--st-font-heading)",
  fontBody: "var(--st-font-body)",
  radius: "var(--st-radius)",
  space: "var(--st-space)",
  maxWidth: "var(--st-max-width)",
} as const;

/** `n` spacing steps, as a CSS length. Keeps every gap on the theme's own scale. */
export function step(n: number): string {
  return `calc(${T.space} * ${n})`;
}
