/**
 * The merchant's theme tokens, as the editor deals with them: which ones are editable, how, and
 * within what bounds. Applying them to the canvas is `@/theme/render`'s `tokenStyle`, which the
 * storefront uses too — one contract for the custom properties, not two.
 *
 * These are the *store's* tokens, not OpsGrids'. The editor chrome around the canvas is Console —
 * `bg-surface`, `text-ink`, the violet accent — and only the canvas contents read the `--st-*`
 * properties `@/theme/render` sets from these values. That boundary is the reason a merchant can
 * pick a colour the console would never use without the console changing colour with it.
 */

import type { ThemeTokens } from "@/theme/schema";

/* Derived from the schema rather than retyped, so a token renamed there fails to compile here
   instead of quietly dropping a control out of the panel. */
type ColorTokenKey = Extract<keyof ThemeTokens, `color${string}`>;
type FontTokenKey = Extract<keyof ThemeTokens, `font${string}`>;
type SizeTokenKey = Exclude<keyof ThemeTokens, ColorTokenKey | FontTokenKey>;

export type TokenFieldDef =
  | { key: ColorTokenKey; kind: "color"; label: string; help?: string }
  | { key: FontTokenKey; kind: "font"; label: string; help?: string }
  | {
      key: SizeTokenKey;
      kind: "size";
      label: string;
      min: number;
      max: number;
      step: number;
      unit: string;
      help?: string;
    };

/** Grouped the way a merchant thinks about them: colour, then type, then shape. */
export const TOKEN_GROUPS: { title: string; fields: TokenFieldDef[] }[] = [
  {
    title: "Colour",
    fields: [
      { key: "colorBg", kind: "color", label: "Page background" },
      { key: "colorSurface", kind: "color", label: "Card background" },
      { key: "colorText", kind: "color", label: "Text" },
      { key: "colorMuted", kind: "color", label: "Secondary text" },
      {
        key: "colorAccent",
        kind: "color",
        label: "Accent",
        help: "Buttons and links. Used sparingly — one accent reads as deliberate, five read as a mistake.",
      },
      { key: "colorAccentText", kind: "color", label: "Text on accent" },
      { key: "colorBorder", kind: "color", label: "Borders" },
    ],
  },
  {
    title: "Type",
    fields: [
      { key: "fontHeading", kind: "font", label: "Headings" },
      { key: "fontBody", kind: "font", label: "Body" },
    ],
  },
  {
    title: "Shape and spacing",
    fields: [
      { key: "radius", kind: "size", label: "Corner radius", min: 0, max: 32, step: 1, unit: "px" },
      {
        key: "space",
        kind: "size",
        label: "Spacing step",
        min: 4,
        max: 32,
        step: 2,
        unit: "px",
        help: "Gutters and padding are multiples of this.",
      },
      {
        key: "maxWidth",
        kind: "size",
        label: "Content width",
        min: 880,
        max: 1600,
        step: 20,
        unit: "px",
      },
    ],
  },
];

/** The font stacks offered. A theme may ship one that is not here; `fontOptions` keeps it. */
const FONT_STACKS: { value: string; label: string }[] = [
  { value: "Manrope, system-ui, sans-serif", label: "Manrope" },
  { value: "Inter, system-ui, sans-serif", label: "Inter" },
  { value: "'IBM Plex Sans', system-ui, sans-serif", label: "IBM Plex Sans" },
  { value: "'Source Serif 4', Georgia, serif", label: "Source Serif" },
  { value: "Georgia, 'Times New Roman', serif", label: "Georgia" },
  { value: "system-ui, -apple-system, 'Segoe UI', sans-serif", label: "System sans" },
  { value: "ui-monospace, 'SF Mono', Menlo, monospace", label: "Monospace" },
];

/**
 * The font choices for a token, with whatever the theme currently uses kept at the top if it is
 * not one of ours — a merchant must never lose their theme's own face by opening the panel.
 */
export function fontOptions(current: string): { value: string; label: string }[] {
  if (FONT_STACKS.some((stack) => stack.value === current)) return FONT_STACKS;
  return [{ value: current, label: `${current.split(",")[0] ?? current} (theme)` }, ...FONT_STACKS];
}
