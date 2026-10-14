import type { CSSProperties } from "react";
import { Link } from "react-router-dom";

import { BLEED_SETTING, STACK_WIDTH } from "../schema";
import { isExternalHref, linkEntries } from "./linkList";
import type { LinkEntry } from "./linkList";
import { pageGutter } from "./pageGrid";
import { MetaTag, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { bool, clampNum, lines, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const COLUMNS = [1, 2, 3, 4] as const;

/** Above this the about block sits beside the link columns instead of above them. */
const WIDE_WIDTH = 1000;

/** Below this the link columns halve — four headings across a 390px phone is four broken words. */
const TIGHT_WIDTH = 560;

function linkStyle(): CSSProperties {
  return {
    fontFamily: T.fontBody,
    fontSize: "0.875rem",
    lineHeight: 1.5,
    color: T.muted,
    textDecoration: "none",
  };
}

/**
 * One entry from a link column.
 *
 * A line with no destination is rendered as text, not as a dead link. That is the whole reason the
 * parser is tolerant: "Mon to Sat, 10am to 7pm" belongs in a contact column and is not a place
 * anyone can go.
 */
function FooterLink({ entry }: { entry: LinkEntry }) {
  if (entry.href === null) {
    return <span style={linkStyle()}>{entry.label}</span>;
  }
  if (isExternalHref(entry.href)) {
    return (
      <a href={entry.href} style={linkStyle()} data-st-target="">
        {entry.label}
      </a>
    );
  }
  return (
    <Link to={entry.href} style={linkStyle()} data-st-target="">
      {entry.label}
    </Link>
  );
}

function LinkColumn({ title, entries }: { title: string | null; entries: LinkEntry[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: step(0.5), minWidth: 0 }}>
      {title ? (
        <StoreHeading level={3} size="sm">
          {title}
        </StoreHeading>
      ) : null}
      {entries.length > 0 ? (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: step(0.35) }}>
          {entries.map((entry) => (
            <li key={`${entry.label}-${entry.href ?? ""}`}>
              <FooterLink entry={entry} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The tall multi-column footer the furniture reference closes on.
 *
 * **A real `<footer>`.** It would have been easy to render a `<div>` and sidestep the fact that
 * the store chrome already draws one — and it would have put the landmark on the wrong element.
 * This section *is* the page's footer when a merchant places it; the chrome's one-line footer is
 * the fallback for a page that has not. `StoreRenderer` stands its own down rather than this one
 * pretending to be something else.
 *
 * **Payment methods are words, in the body face.** We have no licence to Visa's, Mastercard's or
 * NPCI's artwork, shipping a merchant a store that uses it is that merchant's legal problem, and a
 * row of slightly-wrong redrawn card logos looks worse than the words do.
 */
export function FooterColumns({ settings, width }: SectionRenderProps) {
  const aboutTitle = optional(settings, "aboutTitle");
  const aboutText = optional(settings, "aboutText");
  const socialTitle = optional(settings, "socialTitle");
  const social = linkEntries(settings, "socialLinks");
  const paymentTitle = optional(settings, "paymentTitle");
  const payments = lines(settings, "paymentMethods");
  const legal = optional(settings, "legalText");
  const declared = clampNum(settings, "columns", 4, 2, 4);
  const tint = optional(settings, "tint");
  const bleeding = bool(settings, BLEED_SETTING, true);

  const columns = COLUMNS.slice(0, declared)
    .map((slot) => ({
      slot,
      title: optional(settings, `col${slot}Title`),
      entries: linkEntries(settings, `col${slot}Links`),
    }))
    .filter((column) => column.title !== null || column.entries.length > 0);

  const wide = width >= WIDE_WIDTH;
  const perRow = width < TIGHT_WIDTH ? Math.min(columns.length || 1, 2) : Math.max(columns.length, 1);
  const hasAbout = aboutTitle !== null || aboutText !== null;
  const empty =
    !hasAbout && columns.length === 0 && social.length === 0 && payments.length === 0 && legal === null;

  return (
    <footer
      style={{
        background: tint ?? T.surface,
        borderTop: `1px solid ${T.border}`,
        ...(bleeding ? {} : { border: `1px solid ${T.border}`, borderRadius: T.radius }),
        boxSizing: "border-box",
        height: "100%",
        paddingBlock: step(width < STACK_WIDTH ? 1.5 : 2.2),
        paddingInline: bleeding ? pageGutter(width < STACK_WIDTH) : step(1.2),
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: T.maxWidth,
          marginInline: "auto",
          display: "flex",
          flexDirection: "column",
          gap: step(1.4),
        }}
      >
        {empty ? (
          <SectionEmpty
            title="Nothing in the footer yet"
            hint="Add a column heading and some links in this section's settings — one per line, as Label | /link."
          />
        ) : null}

        {hasAbout || columns.length > 0 ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: wide
                ? `minmax(0, 1.4fr) repeat(${Math.max(columns.length, 1)}, minmax(0, 1fr))`
                : `repeat(${perRow}, minmax(0, 1fr))`,
              gap: `${step(1.2)} ${step(1)}`,
              alignItems: "start",
            }}
          >
            {hasAbout ? (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: step(0.5),
                  minWidth: 0,
                  ...(wide ? {} : { gridColumn: "1 / -1" }),
                }}
              >
                {aboutTitle ? (
                  <StoreHeading level={2} size="sm">
                    {aboutTitle}
                  </StoreHeading>
                ) : null}
                {aboutText ? (
                  <StoreText muted small>
                    {aboutText}
                  </StoreText>
                ) : null}
              </div>
            ) : null}
            {columns.map((column) => (
              <LinkColumn key={column.slot} title={column.title} entries={column.entries} />
            ))}
          </div>
        ) : null}

        {social.length > 0 || payments.length > 0 ? (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: `${step(1)} ${step(2)}`,
              justifyContent: "space-between",
              alignItems: "flex-start",
            }}
          >
            {social.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: step(0.5), minWidth: 0 }}>
                {socialTitle ? (
                  <StoreHeading level={3} size="sm">
                    {socialTitle}
                  </StoreHeading>
                ) : null}
                <ul
                  style={{
                    listStyle: "none",
                    margin: 0,
                    padding: 0,
                    display: "flex",
                    flexWrap: "wrap",
                    gap: `${step(0.35)} ${step(0.9)}`,
                  }}
                >
                  {social.map((entry) => (
                    <li key={`${entry.label}-${entry.href ?? ""}`}>
                      <FooterLink entry={entry} />
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {payments.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: step(0.5), minWidth: 0 }}>
                {paymentTitle ? (
                  <StoreHeading level={3} size="sm">
                    {paymentTitle}
                  </StoreHeading>
                ) : null}
                {/* Wordmarks in the theme's body face — see the note on this component. */}
                <ul
                  style={{
                    listStyle: "none",
                    margin: 0,
                    padding: 0,
                    display: "flex",
                    flexWrap: "wrap",
                    gap: step(0.4),
                  }}
                >
                  {payments.map((method) => (
                    <li key={method}>
                      <MetaTag>{method}</MetaTag>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {legal ? (
          <div style={{ borderTop: `1px solid ${T.border}`, paddingTop: step(0.8) }}>
            <StoreText muted small style={{ maxWidth: "none" }}>
              {legal}
            </StoreText>
          </div>
        ) : null}
      </div>
    </footer>
  );
}
