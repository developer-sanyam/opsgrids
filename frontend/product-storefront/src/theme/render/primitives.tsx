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

import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

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
export type HeadingSize = "xl" | "lg" | "md" | "sm";

const HEADING_SIZE: Record<HeadingSize, string> = {
  xl: "clamp(2rem, 4.2vw, 3.1rem)",
  lg: "clamp(1.5rem, 2.6vw, 2.1rem)",
  md: "1.25rem",
  sm: "1rem",
};

/**
 * A heading in the theme's heading face. `level` is the document outline and `size` is the look —
 * separate on purpose, so a visually small heading can still be the right rank for a screen
 * reader.
 */
export function StoreHeading({
  level = 2,
  size = "lg",
  children,
  id,
  style,
}: {
  level?: HeadingLevel;
  size?: HeadingSize;
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
        fontSize: HEADING_SIZE[size],
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

function actionStyle(variant: ActionVariant): CSSProperties {
  return {
    fontFamily: T.fontBody,
    fontSize: "0.9375rem",
    fontWeight: 700,
    lineHeight: 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: `${step(0.8)} ${step(1.3)}`,
    borderRadius: T.radius,
    textDecoration: "none",
    cursor: "pointer",
    background: variant === "solid" ? T.accent : "transparent",
    color: variant === "solid" ? T.accentText : T.text,
    border: `1px solid ${variant === "solid" ? T.accent : T.border}`,
  };
}

/**
 * A themed call to action. Renders an `<a>` for navigation — never a `<button>` that navigates —
 * and routes internal hrefs through the router so the preview does not do a full page load.
 */
export function ActionLink({
  href,
  children,
  variant = "solid",
}: {
  href: string;
  children: ReactNode;
  variant?: ActionVariant;
}) {
  const style = actionStyle(variant);
  const isExternal = /^https?:\/\//.test(href) || href.startsWith("mailto:") || href.startsWith("tel:");
  if (isExternal) {
    return (
      <a href={href} style={style}>
        {children}
      </a>
    );
  }
  return (
    <Link to={href} style={style}>
      {children}
    </Link>
  );
}

/** A themed submit control. A real `<button type="submit">`, with a disabled state. */
export function ActionButton({
  children,
  variant = "solid",
  disabled = false,
  type = "submit",
}: {
  children: ReactNode;
  variant?: ActionVariant;
  disabled?: boolean;
  type?: "submit" | "button";
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      style={{
        ...actionStyle(variant),
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      {children}
    </button>
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

  if (src) return <img src={src} alt={alt ?? ""} style={base} />;

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
