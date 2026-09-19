/**
 * The storefront's own primitives.
 *
 * These deliberately do **not** come from `@opsgrids/ui`. That design system is the Console — the
 * merchant's admin surface — and its components are hard-wired to Console tokens. Everything below
 * renders on the *buyer's* surface, whose palette, type and radius are the merchant's data. A
 * Console `Button` inside a store page would be violet in every theme, which is the opposite of
 * what a theme is for. So: one small set of primitives, every value read from `var(--st-*)`, no
 * literal colour, size or radius anywhere in this folder.
 */

import { useEffect, useId, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { classifyHref } from "./linkList";
import { T, step } from "./tokens";

/* --------------------------------- type ---------------------------------- */

/**
 * The small label above a heading.
 *
 * `tone="inherit"` exists for the one real case: a hero on an accent or contrast ground, where the
 * theme's accent on the theme's accent is unreadable. There the eyebrow takes the surrounding
 * foreground instead, which is the only pair guaranteed to pass on that ground.
 */
export function Eyebrow({
  children,
  tone = "accent",
}: {
  children: ReactNode;
  tone?: "accent" | "inherit";
}) {
  return (
    <span
      style={{
        fontFamily: T.fontBody,
        color: tone === "accent" ? T.accent : "inherit",
        fontSize: "0.75rem",
        fontWeight: 700,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
      }}
    >
      {children}
    </span>
  );
}

export type HeadingLevel = 2 | 3 | 4;
export type HeadingSize = "display" | "xl" | "lg" | "md" | "sm";

/**
 * The three fluid steps, measured against the **canvas** rather than the viewport.
 *
 * `vw` is the browser window, but a storefront canvas is a fixed-width box — 390px in the editor's
 * phone preview, a scaled 1280 in the gallery's miniature — so every section heading in the library
 * rendered at full desktop size inside a phone frame, and the one place a merchant checks their
 * mobile layout was the place that lied about it. Same class of defect as the rail card width, the
 * bleed calculation and `Hero`'s own `h1`; this is the last of them, and `Hero` is the pattern.
 *
 * The slopes are the old `vw` numbers divided by 100, so a real browser at full width is unmoved:
 * `canvas × 0.026` is exactly what `2.6vw` resolved to when the store filled the window. Floor and
 * ceiling stay in `rem` deliberately — they have to keep scaling with a reader's own default font
 * size, which a computed px value would freeze.
 *
 * `display` is the fifth step, and new. `xl` bottoms out at 2rem — right for a section title and
 * far too small for the closing wordmark block the furniture reference ends on, where the whole
 * point is that the name is enormous. Nothing below it moved: raising `xl` instead would have
 * resized headings across all six trade themes to fix one block in one theme.
 */
const HEADING_RAMP = {
  display: { floor: "2.75rem", slope: 0.075, ceiling: "5.5rem" },
  xl: { floor: "2rem", slope: 0.042, ceiling: "3.1rem" },
  lg: { floor: "1.5rem", slope: 0.026, ceiling: "2.1rem" },
} as const satisfies Partial<Record<HeadingSize, { floor: string; slope: number; ceiling: string }>>;

/** The two steps that were never fluid. A caption does not want to grow with the page. */
const HEADING_FIXED = { md: "1.25rem", sm: "1rem" } as const;

/**
 * The `font-size` for a step, against a canvas width when the caller knows one.
 *
 * Without a width the old viewport ramp is kept rather than guessed at: a caller that has not been
 * threaded yet keeps exactly today's behaviour instead of silently collapsing to the floor.
 */
function headingFontSize(size: HeadingSize, width?: number): string {
  if (size === "md" || size === "sm") return HEADING_FIXED[size];
  const ramp = HEADING_RAMP[size];
  if (width === undefined) {
    return `clamp(${ramp.floor}, ${Math.round(ramp.slope * 1000) / 10}vw, ${ramp.ceiling})`;
  }
  // One decimal, so the ramp lands on what the `vw` term resolved to rather than near it, while
  // the declaration stays readable in devtools.
  const fluid = Math.max(0, Math.round(width * ramp.slope * 10) / 10);
  return `clamp(${ramp.floor}, ${fluid}px, ${ramp.ceiling})`;
}

/**
 * A heading in the theme's heading face. `level` is the document outline and `size` is the look —
 * separate on purpose, so a visually small heading can still be the right rank for a screen
 * reader.
 */
export function StoreHeading({
  level = 2,
  size = "lg",
  width,
  children,
  id,
  style,
}: {
  level?: HeadingLevel;
  size?: HeadingSize;
  /**
   * The canvas width, from `SectionRenderProps.width`. Pass it: without it the heading falls back
   * to a `vw` ramp that measures the browser window and so renders a phone preview at desktop size.
   */
  width?: number;
  children: ReactNode;
  id?: string;
  style?: CSSProperties;
}) {
  const Tag = (level === 2 ? "h2" : level === 3 ? "h3" : "h4") as "h2" | "h3" | "h4";
  return (
    <Tag
      {...(id ? { id } : {})}
      style={{
        fontFamily: T.fontHeading,
        color: T.text,
        fontSize: headingFontSize(size, width),
        fontWeight: 700,
        lineHeight: 1.12,
        letterSpacing: "-0.02em",
        textWrap: "balance",
        margin: 0,
        ...style,
      }}
    >
      {children}
    </Tag>
  );
}

export function StoreText({
  children,
  muted = false,
  small = false,
  style,
}: {
  children: ReactNode;
  muted?: boolean;
  small?: boolean;
  style?: CSSProperties;
}) {
  return (
    <p
      style={{
        fontFamily: T.fontBody,
        color: muted ? T.muted : T.text,
        fontSize: small ? "0.8125rem" : "1rem",
        lineHeight: 1.6,
        maxWidth: "65ch",
        margin: 0,
        ...style,
      }}
    >
      {children}
    </p>
  );
}

/* -------------------------------- surfaces -------------------------------- */

export function Panel({
  children,
  style,
  padded = true,
}: {
  children: ReactNode;
  style?: CSSProperties;
  padded?: boolean;
}) {
  return (
    <div
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        padding: padded ? step(1.5) : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** A small qualifying label — availability, GST basis, MOQ. The words carry it, not the tint. */
export function MetaTag({ children, tone = "quiet" }: { children: ReactNode; tone?: "quiet" | "accent" }) {
  return (
    <span
      style={{
        fontFamily: T.fontBody,
        fontSize: "0.75rem",
        fontWeight: 600,
        color: tone === "accent" ? T.accentText : T.muted,
        background: tone === "accent" ? T.accent : "transparent",
        border: tone === "accent" ? `1px solid ${T.accent}` : `1px solid ${T.border}`,
        borderRadius: `calc(${T.radius} / 2)`,
        padding: `${step(0.15)} ${step(0.45)}`,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

/* --------------------------------- actions -------------------------------- */

export type ActionVariant = "solid" | "outline";

/**
 * How much room an action takes.
 *
 * `md` is the page-level call to action and stays the default, so nothing already built moves.
 * `sm` exists for controls that sit inside a card — a retail ADD next to a pack-size selector at
 * 390px cannot wear the padding a hero button wears, and at `space: 18` the difference is the
 * whole card.
 */
export type ActionSize = "sm" | "md";

/**
 * The padding stays split into its two halves rather than written as one shorthand because
 * `QuantityStepper` has to reproduce the vertical half exactly: a stepper that replaces an action
 * and stands one pixel taller moves every card in the row the first time a buyer presses ADD.
 */
const ACTION_SIZE: Record<ActionSize, { fontSize: string; padY: string; padX: string }> = {
  sm: { fontSize: "0.8125rem", padY: step(0.45), padX: step(0.85) },
  md: { fontSize: "0.9375rem", padY: step(0.8), padX: step(1.3) },
};

/**
 * Which pair of colours an action takes.
 *
 * `theme` is the ordinary case: an outline action is the theme's text inside the theme's border.
 * `inherit` is for an action sitting on something that is *not* the page ground — a hero's scrim, a
 * band in the accent — where `T.text` is the wrong end of the pair and can land unreadable. There
 * the action takes the surrounding foreground for both its words *and* its border, which is the one
 * pair guaranteed to pass wherever the caller has already settled the contrast. `Eyebrow` carries
 * the same option for the same reason.
 */
export type ActionTone = "theme" | "inherit";

function actionStyle(variant: ActionVariant, size: ActionSize, tone: ActionTone): CSSProperties {
  // Only the outline has a pair to get wrong; a solid action paints its own ground and carries the
  // accent's own text colour with it, on a scrim or anywhere else.
  const inherited = tone === "inherit" && variant === "outline";
  return {
    fontFamily: T.fontBody,
    fontSize: ACTION_SIZE[size].fontSize,
    fontWeight: 700,
    lineHeight: 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: `${ACTION_SIZE[size].padY} ${ACTION_SIZE[size].padX}`,
    borderRadius: T.radius,
    textDecoration: "none",
    cursor: "pointer",
    background: variant === "solid" ? T.accent : "transparent",
    color: variant === "solid" ? T.accentText : inherited ? "inherit" : T.text,
    border: `1px solid ${variant === "solid" ? T.accent : inherited ? "currentColor" : T.border}`,
  };
}

/**
 * A themed call to action. Renders an `<a>` for navigation — never a `<button>` that navigates —
 * and routes internal hrefs through the router so the preview does not do a full page load.
 *
 * **The href goes through `classifyHref` and nothing else.** Every section's CTA is merchant-typed
 * data, so the one scheme allow-list in `linkList` decides here exactly as it decides for a tile
 * and for a footer column. A destination it refuses renders the merchant's own words in a `<span>`
 * wearing the disabled treatment `ActionButton` already uses: not an anchor, so there is no tap to
 * swallow and nothing focusable that goes nowhere, and not nothing either, so the merchant sees
 * their call to action sitting there inert instead of silently losing it.
 */
export function ActionLink({
  href,
  children,
  variant = "solid",
  size = "md",
  tone = "theme",
  ariaLabel,
}: {
  href: string;
  children: ReactNode;
  variant?: ActionVariant;
  size?: ActionSize;
  /** `inherit` for an action on a scrim or a band, where the page's own text colour is wrong. */
  tone?: ActionTone;
  /** A fuller accessible name than the visible words — "View all, Fasteners", not "View all". */
  ariaLabel?: string;
}) {
  const style = actionStyle(variant, size, tone);
  const named = ariaLabel ? { "aria-label": ariaLabel } : {};
  const decision = classifyHref(href);
  if (decision.kind === "refused") {
    /* No `aria-label` here on purpose: a name belongs to a control, and this is no longer one —
       on a `<span>` with no role it is ignored by some screen readers anyway, which would leave
       the words silent for them. The visible words stay in the DOM and are read as text. */
    return <span style={{ ...style, opacity: 0.55, cursor: "default" }}>{children}</span>;
  }
  if (decision.kind === "external") {
    return (
      <a href={decision.href} style={style} {...named}>
        {children}
      </a>
    );
  }
  return (
    <Link to={decision.href} style={style} {...named}>
      {children}
    </Link>
  );
}

/** A themed submit control. A real `<button type="submit">`, with a disabled state. */
export function ActionButton({
  children,
  variant = "solid",
  size = "md",
  tone = "theme",
  disabled = false,
  type = "submit",
  ariaLabel,
  onClick,
}: {
  children: ReactNode;
  variant?: ActionVariant;
  size?: ActionSize;
  /** `inherit` for an action on a scrim or a band, where the page's own text colour is wrong. */
  tone?: ActionTone;
  disabled?: boolean;
  type?: "submit" | "button";
  /**
   * A fuller accessible name than the visible words. A grid of twenty cards each offering "ADD"
   * is unusable by screen reader or by voice, so the card passes the product name through here.
   * The visible words stay inside the name, which is what keeps voice control working.
   */
  ariaLabel?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      {...(ariaLabel ? { "aria-label": ariaLabel } : {})}
      {...(onClick ? { onClick } : {})}
      style={{
        ...actionStyle(variant, size, tone),
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      {children}
    </button>
  );
}

/**
 * A quantity, with one control to raise it and one to lower it.
 *
 * **Two real `<button>`s and a number**, not a text field. A buyer standing in a shop taps; a
 * number input would put a keyboard in their way, accept "-3" and "1e9", and then need every one of
 * those cases handled. The quantity the buttons produce is always a quantity the list can hold.
 *
 * It is sized to land exactly where the action it replaces stood: same font size, same vertical
 * padding, same one-pixel border, so pressing ADD swaps one for the other and moves nothing else on
 * the page. That is why `ACTION_SIZE` keeps its padding in halves.
 *
 * Both names arrive complete from the caller — "Add one more Amul Gold 1 L", not "+". A grid of
 * twenty cards each offering "+" cannot be used by voice or by screen reader at all.
 */
export function QuantityStepper({
  value,
  decreaseLabel,
  increaseLabel,
  onDecrease,
  onIncrease,
  size = "md",
}: {
  /** Already formatted by the caller — this primitive does no arithmetic and no wording. */
  value: string;
  decreaseLabel: string;
  increaseLabel: string;
  onDecrease: () => void;
  onIncrease: () => void;
  size?: ActionSize;
}) {
  const { fontSize, padY, padX } = ACTION_SIZE[size];
  const buttonStyle: CSSProperties = {
    fontFamily: T.fontBody,
    fontSize,
    fontWeight: 700,
    lineHeight: 1,
    padding: `${padY} ${padX}`,
    background: "transparent",
    color: "inherit",
    border: "none",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "stretch",
        // The two controls go to the ends when a caller stretches this across a card's action row,
        // which is the shape the quick-commerce reference uses once a line is open.
        justifyContent: "space-between",
        background: T.accent,
        color: T.accentText,
        border: `1px solid ${T.accent}`,
        borderRadius: T.radius,
        /* No `overflow: hidden` here. The two buttons paint no ground of their own, so nothing
           needs clipping to the rounded corners — and clipping would cut the focus ring off at the
           ends of the control, which is where it matters most. */
      }}
    >
      <button type="button" aria-label={decreaseLabel} onClick={onDecrease} style={buttonStyle}>
        −
      </button>
      {/* Tabular figures and a floor on the width, so stepping 9 → 10 does not shove the two
          buttons sideways under the buyer's thumb. */}
      <span
        style={{
          fontFamily: T.fontBody,
          fontSize,
          fontWeight: 700,
          lineHeight: 1,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flex: "1 1 auto",
          minWidth: "2.5ch",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
      <button type="button" aria-label={increaseLabel} onClick={onIncrease} style={buttonStyle}>
        +
      </button>
    </span>
  );
}

/* ---------------------------------- forms ---------------------------------- */

/**
 * A single-line text field on the buyer's surface: a real `<input>`, a real `<label>` tied to it,
 * and its error tied to it by `aria-describedby`.
 *
 * It exists because three sections were about to hand-roll the same twelve style declarations —
 * the announcement bar's search, the enquiry form's fields and the delivery check's postal code —
 * and the third of those would have been the first one to get `aria-invalid` wrong.
 *
 * **An error is marked by weight, position and wording, never by a tint.** `ThemeTokens` names one
 * accent and no semantic colour, so a red-for-danger does not exist to reach for: painting an
 * error in the accent would read as "danger" in one theme and as "this is the brand" in the next.
 * The border does move to `currentColor` weight-wise — it thickens — which is a non-colour signal
 * that survives both a monochrome theme and a buyer who cannot distinguish the hues.
 *
 * `hideLabel` moves the label into the accessible name rather than hiding it with CSS: a promise
 * bar has no room for "PIN code" stacked above a six-character field, and the control still has to
 * be named. It does not drop the name — an `<input>` with no label is unusable by voice control.
 */
export function StoreField({
  id,
  label,
  value,
  onChange,
  name,
  error,
  placeholder,
  inputMode,
  autoComplete,
  maxLength,
  hideLabel = false,
  reserveError = false,
  style,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  name?: string;
  /** The message, already written. Present means the field is invalid. */
  error?: string | null;
  placeholder?: string;
  inputMode?: "text" | "numeric";
  autoComplete?: string;
  maxLength?: number;
  hideLabel?: boolean;
  /**
   * Hold the error's line whether or not there is an error in it.
   *
   * For a field inside page chrome — a bar across the top of a store — where the message appearing
   * would otherwise push everything below it down under the buyer's thumb as they type. Off by
   * default: inside a form that is already the whole column, the reserved line is dead space on
   * every field that never errors.
   */
  reserveError?: boolean;
  style?: CSSProperties;
}) {
  const errorId = `${id}-error`;
  const invalid = typeof error === "string" && error !== "";

  return (
    <span style={{ display: "flex", flexDirection: "column", gap: step(0.25), minWidth: 0, ...style }}>
      {hideLabel ? null : (
        <label
          htmlFor={id}
          style={{ fontFamily: T.fontBody, fontSize: "0.75rem", fontWeight: 600, color: T.muted }}
        >
          {label}
        </label>
      )}
      <input
        id={id}
        type="text"
        {...(name ? { name } : {})}
        {...(hideLabel ? { "aria-label": label } : {})}
        {...(placeholder ? { placeholder } : {})}
        {...(inputMode ? { inputMode } : {})}
        {...(autoComplete ? { autoComplete } : {})}
        {...(maxLength === undefined ? {} : { maxLength })}
        {...(invalid ? { "aria-invalid": true, "aria-describedby": errorId } : {})}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        style={{
          fontFamily: T.fontBody,
          fontSize: "0.9375rem",
          color: T.text,
          background: T.bg,
          border: `${invalid ? "2px" : "1px"} solid ${T.border}`,
          borderRadius: T.radius,
          padding: invalid ? `calc(${step(0.55)} - 1px) calc(${step(0.65)} - 1px)` : `${step(0.55)} ${step(0.65)}`,
          minWidth: 0,
          width: "100%",
          boxSizing: "border-box",
        }}
      />
      {invalid || reserveError ? (
        <span
          {...(invalid ? { id: errorId } : {})}
          style={{
            fontFamily: T.fontBody,
            fontSize: "0.75rem",
            fontWeight: 700,
            color: T.text,
            lineHeight: 1.35,
            ...(reserveError ? { minHeight: "1.05rem" } : {}),
          }}
        >
          {invalid ? error : null}
        </span>
      ) : null}
    </span>
  );
}

/* --------------------------------- retail --------------------------------- */

/**
 * The angled corner flag over a product photo.
 *
 * The saving is written out — "27% OFF" — because the accent ground is a theme's data and cannot
 * be relied on to mean "discount" in every palette. A buyer who cannot see the tint still reads
 * the number; the flag shape is decoration on top of a sentence, not the sentence.
 */
export function DiscountBadge({ percent, style }: { percent: number; style?: CSSProperties }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: T.fontBody,
        fontSize: "0.6875rem",
        fontWeight: 800,
        lineHeight: 1.15,
        letterSpacing: "0.02em",
        textAlign: "center",
        whiteSpace: "nowrap",
        color: T.accentText,
        background: T.accent,
        padding: `${step(0.25)} ${step(0.4)} ${step(0.5)}`,
        clipPath: "polygon(0 0, 100% 0, 100% 100%, 50% 82%, 0 100%)",
        ...style,
      }}
    >
      {percent}% OFF
    </span>
  );
}

/**
 * Text that is read but not seen. One definition, so the clip trick is not re-invented per section.
 */
const OFF_SCREEN: CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clipPath: "inset(50%)",
  whiteSpace: "nowrap",
};

/**
 * Text that is read but not seen, as a component.
 *
 * Exported because the alternative is worse in both directions: a section that needs off-screen text
 * either writes the clip trick itself — which means a literal `1px` inside `render/`, where no
 * literal size may appear — or replaces the text with an `aria-label`, which silently drops it for
 * everyone using voice control or reading the page at 400% zoom.
 *
 * `live` makes it a polite live region. The element renders whether or not it has anything in it,
 * which matters: a live region announced only once it appears is a live region a screen reader was
 * not watching when the change happened.
 */
export function VisuallyHidden({
  children,
  live,
}: {
  children: ReactNode;
  live?: "polite" | "assertive";
}) {
  return (
    <span style={OFF_SCREEN} {...(live ? { "aria-live": live } : {})}>
      {children}
    </span>
  );
}

/**
 * A selling price beside the MRP it is struck off.
 *
 * A real `<s>`, so the strike is in the markup and not only in the paint. Two bare amounts read one
 * after the other tell a screen-reader user nothing about which one they pay, so the pair carries
 * an accessible name — "₹69, was ₹99".
 *
 * The word "was" is *also* in the DOM, off screen, and the numbers are not `aria-hidden`. That is
 * deliberate belt and braces: `aria-label` on an element with no role is not honoured by every
 * screen reader, and hiding the children behind a name that some readers ignore would leave the
 * price silent — a worse failure than hearing it twice.
 *
 * Both strings arrive already formatted: money is divided by 100 in `lib/format` and nowhere else.
 */
export function PriceWithMrp({
  price,
  mrp,
  style,
}: {
  price: string;
  mrp?: string | null;
  style?: CSSProperties;
}) {
  return (
    <span
      aria-label={mrp ? `${price}, was ${mrp}` : price}
      style={{ display: "inline-flex", alignItems: "baseline", gap: step(0.35), flexWrap: "wrap", ...style }}
    >
      <span
        style={{
          fontFamily: T.fontHeading,
          fontSize: "1.05rem",
          fontWeight: 700,
          color: T.text,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {price}
      </span>
      {mrp ? <span style={OFF_SCREEN}>, was</span> : null}
      {mrp ? (
        <s
          style={{
            fontFamily: T.fontBody,
            fontSize: "0.8125rem",
            color: T.muted,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {mrp}
        </s>
      ) : null}
    </span>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/**
 * The pack-size selector: a real `<select>`.
 *
 * Not a custom popover. The native control is keyboard-operable, announces its own value and
 * position, and on a phone opens the platform picker — all of which a themed `div` would have to
 * re-implement and would get wrong. Only its skin comes from the theme.
 *
 * `hideLabel` moves the label into the accessible name rather than hiding it with CSS: the card
 * has no room for "Pack size" above a 500 g dropdown, but the control still has to be named.
 */
export function VariantSelect({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
  id,
  style,
}: {
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange?: (value: string) => void;
  hideLabel?: boolean;
  id?: string;
  style?: CSSProperties;
}) {
  const generated = useId();
  const selectId = id ?? generated;
  /* Controlled only when someone is listening. A `value` with no `onChange` is a React warning and
     a control the buyer cannot move; without a handler the native element keeps its own state. */
  const binding = onChange
    ? { value, onChange: (event: { target: { value: string } }) => onChange(event.target.value) }
    : { defaultValue: value };

  const select = (
    <select
      id={selectId}
      {...binding}
      {...(hideLabel ? { "aria-label": label } : {})}
      style={{
        fontFamily: T.fontBody,
        fontSize: "0.8125rem",
        fontWeight: 600,
        color: T.text,
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: `calc(${T.radius} / 2)`,
        padding: `${step(0.3)} ${step(0.4)}`,
        maxWidth: "100%",
        cursor: "pointer",
        ...style,
      }}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled ?? false}>
          {option.label}
        </option>
      ))}
    </select>
  );

  if (hideLabel) return select;

  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: step(0.2) }}>
      <label
        htmlFor={selectId}
        style={{ fontFamily: T.fontBody, fontSize: "0.75rem", fontWeight: 600, color: T.muted }}
      >
        {label}
      </label>
      {select}
    </span>
  );
}

/* --------------------------------- imagery -------------------------------- */

function seedOf(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) % 9973;
  return hash;
}

/**
 * The stand-in for a merchant's photography.
 *
 * No stock photos and no external image host: an unset `image` field draws a deterministic plate
 * from the theme's own tokens, so a store with no uploads still looks composed and a theme's
 * preview is honest about what it ships. `alt=""` and `aria-hidden` — it is decoration, and
 * announcing "abstract plate" to a screen reader would be noise.
 */
export function ImagePlate({
  seed,
  src,
  alt,
  style,
}: {
  seed: string;
  src?: string | null;
  alt?: string;
  style?: CSSProperties;
}) {
  /* An address that does not load falls back to the generated plate rather than the browser's
     broken-image icon. A merchant who purges an asset a page still references would otherwise see
     a torn-page glyph on their live store — and because deletion is soft for thirty days, it would
     appear long after the action that caused it, which is the hardest kind of fault to connect.
     Keyed on `src` so replacing a bad address with a good one retries instead of staying broken. */
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [src]);

  const base: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: step(6),
    borderRadius: T.radius,
    border: `1px solid ${T.border}`,
    objectFit: "cover",
    display: "block",
    ...style,
  };

  if (src && !failed) {
    return <img src={src} alt={alt ?? ""} style={base} onError={() => setFailed(true)} />;
  }

  const variant = seedOf(seed) % 4;
  return (
    <svg viewBox="0 0 160 100" preserveAspectRatio="none" style={base} aria-hidden focusable="false">
      <rect width="160" height="100" fill={T.surface} />
      {variant === 0 ? (
        <>
          <rect x="0" y="62" width="160" height="38" fill={T.accent} opacity="0.18" />
          <rect x="18" y="26" width="46" height="74" fill={T.accent} opacity="0.55" />
          <rect x="72" y="44" width="30" height="56" fill={T.muted} opacity="0.35" />
          <rect x="110" y="14" width="34" height="86" fill={T.accent} opacity="0.3" />
        </>
      ) : null}
      {variant === 1 ? (
        <>
          <circle cx="54" cy="50" r="34" fill={T.accent} opacity="0.35" />
          <circle cx="54" cy="50" r="15" fill={T.surface} />
          <circle cx="112" cy="50" r="22" fill={T.muted} opacity="0.3" />
          <circle cx="112" cy="50" r="9" fill={T.surface} />
        </>
      ) : null}
      {variant === 2 ? (
        <>
          <path d="M0 78 L40 34 L80 78 L120 24 L160 78 Z" fill={T.accent} opacity="0.32" />
          <rect x="0" y="78" width="160" height="22" fill={T.muted} opacity="0.28" />
        </>
      ) : null}
      {variant === 3 ? (
        <>
          <rect x="10" y="10" width="64" height="36" fill={T.accent} opacity="0.5" />
          <rect x="82" y="10" width="68" height="36" fill={T.muted} opacity="0.25" />
          <rect x="10" y="54" width="68" height="36" fill={T.muted} opacity="0.25" />
          <rect x="86" y="54" width="64" height="36" fill={T.accent} opacity="0.3" />
        </>
      ) : null}
    </svg>
  );
}

/* --------------------------------- layout --------------------------------- */

/** A section's internal stack — the one place vertical rhythm inside a section is decided. */
export function Stack({
  children,
  gap = 1,
  style,
}: {
  children: ReactNode;
  gap?: number;
  style?: CSSProperties;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: step(gap), ...style }}>
      {children}
    </div>
  );
}

/** A section's own empty state: says what is missing and, for the merchant, why. */
export function SectionEmpty({ title, hint }: { title: string; hint: string }) {
  return (
    <div
      style={{
        border: `1px dashed ${T.border}`,
        borderRadius: T.radius,
        padding: step(1.5),
        display: "flex",
        flexDirection: "column",
        gap: step(0.25),
      }}
    >
      <StoreHeading level={3} size="sm">
        {title}
      </StoreHeading>
      <StoreText muted small>
        {hint}
      </StoreText>
    </div>
  );
}
