import type { CSSProperties } from "react";

import { Eyebrow, SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { longFormHeadings, parseLongForm, type LongFormBlock } from "./prose";
import { bool, choice, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const MEASURES = ["comfortable", "wide", "full"] as const;

/**
 * How wide the prose sets.
 *
 * In `ch`, because the measure that matters is characters per line and `ch` is the unit that keeps
 * it right when a merchant picks a wide body face or a reader raises their own default font size —
 * a px cap would silently be a 45-character column on one theme and a 100-character one on another.
 * `full` is offered because a merchant who has already placed this block in a narrow grid column
 * does not want it narrowed twice.
 *
 * **The numbers are smaller than the character counts they produce**, and that is not a mistake:
 * `1ch` is the width of a zero, which is wider than the average letter. Measured in Chrome on the
 * trade theme, a 50ch cap set about 70 characters to the line — the number the label quotes — and
 * the 68ch this first shipped with ran to about 95, which is past the point where the eye loses
 * the start of the next line. That is precisely the defect jsdom cannot see.
 */
const MEASURE: Record<(typeof MEASURES)[number], string> = {
  comfortable: "50ch",
  wide: "62ch",
  full: "none",
};

/**
 * Structured prose from a single authored field — the section that carries the nine legal pages,
 * the shipping, returns, cancellation, replacement and warranty policies, and the support pages.
 *
 * The convention (`## `, `- `, `1. `, blank line) is parsed by `prose.ts` and rendered here as
 * real `<h2>`/`<h3>`, `<p>`, `<ul>` and `<ol>`. **No markdown dependency and no
 * `dangerouslySetInnerHTML`**: a store document is merchant data that a renderer must never be
 * able to turn into markup, and a policy page is not worth a parser in the bundle.
 *
 * **Subheading rank moves with the section's own heading.** A `long-form` with a heading of its own
 * puts that heading at `h2` and its subheadings at `h3`; one without a heading — the usual shape,
 * sitting under a `page-header` that already carries the page title — puts the subheadings at `h2`.
 * Either way the outline is `h1 → h2 → h3` with nothing skipped, which is what a screen reader's
 * heading list is for.
 */
export function LongForm({ settings, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const updated = optional(settings, "updated");
  const showToc = bool(settings, "showToc", false);
  const tocLabel = str(settings, "tocLabel", "On this page").trim();
  const measure = choice(settings, "measure", MEASURES, "comfortable");
  const blocks = parseLongForm(str(settings, "body"));
  const headings = longFormHeadings(blocks);

  const subLevel = heading === null ? 2 : 3;
  const maxWidth = MEASURE[measure];

  const bodyStyle: CSSProperties = {
    fontFamily: T.fontBody,
    fontSize: "1rem",
    lineHeight: 1.65,
    color: T.text,
  };

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {updated ? (
        <StoreText muted small>
          {updated}
        </StoreText>
      ) : null}

      {showToc && headings.length > 0 ? (
        /* A real `<nav>` with its own name, so a screen reader can jump straight to it and past it.
           The visible label is not a heading: a "Contents" heading sitting above the subheadings it
           links to would be either the wrong rank or a second `h2` that means nothing in the
           outline. `aria-label` carries the same words to anyone who cannot see them. */
        <nav
          aria-label={tocLabel === "" ? "On this page" : tocLabel}
          style={{
            background: T.surface,
            border: `1px solid ${T.border}`,
            borderRadius: T.radius,
            padding: step(1),
            display: "flex",
            flexDirection: "column",
            gap: step(0.5),
            maxWidth,
          }}
        >
          {tocLabel === "" ? null : <Eyebrow>{tocLabel}</Eyebrow>}
          {/* An `<ol>`, because the contents are in document order and that is information — but
              with no marker: a Terms page numbers its own clauses ("7. Governing law"), and the
              list's marker on top of that reads "7. 7. Governing law". Seen in Chrome. */}
          <ol
            style={{
              margin: 0,
              padding: 0,
              listStyle: "none",
              display: "flex",
              flexDirection: "column",
              gap: step(0.25),
              ...bodyStyle,
              fontSize: "0.9375rem",
            }}
          >
            {headings.map((entry) => (
              <li key={entry.slug}>
                {/* A plain `<a>`, not a router `Link`: this is a fragment on the page already
                    open, and handing "#refunds" to the router would be read as a path. */}
                <a href={`#${entry.slug}`} style={{ color: T.text }}>
                  {entry.text}
                </a>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}

      {blocks.length === 0 ? (
        <SectionEmpty
          title="No text yet"
          hint="Write the page in this section's Body setting. Start a line with ## for a subheading, with - for a bullet."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: step(0.75), maxWidth }}>
          {blocks.map((block, index) => (
            <Block
              key={`${block.kind}-${index}`}
              block={block}
              level={subLevel}
              width={width}
              bodyStyle={bodyStyle}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function Block({
  block,
  level,
  width,
  bodyStyle,
}: {
  block: LongFormBlock;
  level: 2 | 3;
  width: number;
  bodyStyle: CSSProperties;
}) {
  if (block.kind === "heading") {
    return (
      <StoreHeading
        level={level}
        size="md"
        width={width}
        id={block.slug}
        style={{
          // So a jump from the table of contents does not land the heading tight against the top
          // of the window with its first paragraph hidden behind any sticky chrome.
          scrollMarginTop: step(2),
          marginTop: step(0.75),
        }}
      >
        {block.text}
      </StoreHeading>
    );
  }

  if (block.kind === "paragraph") {
    // The measure is set by the container, so the primitive's own 65ch cap is stood down here
    // rather than fighting it.
    return <StoreText style={{ ...bodyStyle, maxWidth: "none" }}>{block.text}</StoreText>;
  }

  const List = block.ordered ? "ol" : "ul";
  return (
    <List
      style={{
        ...bodyStyle,
        margin: 0,
        paddingLeft: step(1.4),
        display: "flex",
        flexDirection: "column",
        gap: step(0.3),
      }}
    >
      {block.items.map((item) => (
        <li key={item.slice(0, 60)}>{item}</li>
      ))}
    </List>
  );
}
