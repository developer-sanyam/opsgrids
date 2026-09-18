import { useState } from "react";

import { isSafeHref } from "./linkList";
import { ImagePlate, SectionEmpty, StoreHeading, StoreText, VisuallyHidden } from "./primitives";
import { clampNum, optional, str } from "./settings";
import { TileShell } from "./TileShell";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/** Four slots. More films than that is a video library — records and a search box, not settings. */
const SLOTS = [1, 2, 3, 4] as const;

/** Every film is 16:9. This is not a merchant choice; it is what the frame is. */
const FRAME = "16 / 9";

/**
 * Whether a merchant's embed address may be put in an `src`.
 *
 * **This is the security boundary of the section.** A store document is merchant-authored data that
 * a past build, an import, or a compromised console session could have written; an `src` taken from
 * it unchecked is an injection point, because `javascript:` in an iframe's `src` executes on the
 * *store's* origin. `https:` only — not `http:` (a mixed-content frame a browser blocks anyway, and
 * a downgrade on a page that takes orders), not a protocol-relative `//host`, not a data URL.
 *
 * `new URL()` does the parsing rather than a regular expression, so no leading-whitespace or
 * embedded-newline trick gets a scheme past a pattern that looked right.
 */
function embedSource(value: string | null): string | null {
  if (value === null) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  /* The one change made to what the merchant pasted: the visitor has already pressed play, so the
     film should start rather than needing a second press inside the frame. Both major hosts read
     this parameter and a host that does not simply ignores it — no provider is being guessed at,
     and no ID is being parsed out of a URL shape we would only get right for two of them. */
  url.searchParams.set("autoplay", "1");
  return url.toString();
}

/**
 * The Videos page.
 *
 * **The decision, which is the point of this file.**
 *
 * A YouTube or Vimeo `<iframe>` is a third-party script and a set of third-party cookies, dropped
 * onto a merchant's storefront the moment the page loads — on every visitor, whether or not anyone
 * watches anything. In a codebase whose imagery is deliberately self-hosted (`ImagePlate` draws a
 * plate from the theme's own tokens rather than fetching stock art), whose logo wall renders
 * *words* because it has no licence to anyone's marks, and whose footer lists payment methods as
 * words for the same reason, a silent third-party embed would be the one place the product quietly
 * took a decision on the merchant's behalf — and it is the merchant, not us, who answers for it
 * under their own privacy policy.
 *
 * So: **a click-to-play facade**. Nothing third-party exists in the document until a visitor
 * presses play. Before that the block is a poster the *merchant uploaded* — never a still fetched
 * from the host, which would be the same third-party request wearing a different hat — a title, a
 * detail line, and a plain link out. The frame is created by the press, and the privacy line under
 * it says so before it is pressed.
 *
 * It is not "nothing", because a Videos page with no video on it is not a Videos page; and it is
 * not a bare link out, because leaving the store to watch a two-minute factory tour is a visitor
 * who does not come back. A merchant who wants only the link out already has it: leave the embed
 * address empty and the poster becomes the link.
 *
 * One film plays at a time. Pressing play on a second closes the first, which is the behaviour a
 * gallery of films needs anyway — two soundtracks at once is nobody's design.
 */
export function VideoList({ settings, stacked, width }: SectionRenderProps) {
  /* The only state in the pack. It is the facade's whole mechanism and it is per-instance: no
     provider, no context, no knowledge of the page — the section still renders correctly from its
     props alone, which is the contract in `types.ts`. */
  const [playing, setPlaying] = useState<number | null>(null);

  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 2, 1, 3);
  const playLabel = str(settings, "playLabel", "Play").trim() || "Play";
  const watchLabel = str(settings, "watchLabel", "").trim();
  const privacyNote = optional(settings, "privacyNote");

  const films = SLOTS.map((slot) => ({
    slot,
    poster: optional(settings, `item${slot}Poster`),
    title: optional(settings, `item${slot}Title`),
    meta: optional(settings, `item${slot}Meta`),
    href: optional(settings, `item${slot}Href`),
    embed: embedSource(optional(settings, `item${slot}Embed`)),
  })).filter((film) => film.title !== null);

  // One column on a phone whatever the setting says: a 16:9 frame in half of 390px is 170px wide,
  // which is not a film anyone watches.
  const cols = stacked ? 1 : columns;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1.1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {films.length === 0 ? (
        <SectionEmpty
          title="No videos yet"
          hint="Give a video a title and upload a poster image in this section's settings. A film with no title is not shown."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(cols, films.length)}, minmax(0, 1fr))`,
            gap: step(1),
          }}
        >
          {films.map((film) => {
            const playable = film.embed !== null;
            const open = playing === film.slot && film.embed !== null;
            const title = film.title ?? "";

            return (
              <li key={film.slot} style={{ display: "flex", flexDirection: "column", gap: step(0.5) }}>
                <div
                  style={{
                    position: "relative",
                    aspectRatio: FRAME,
                    borderRadius: T.radius,
                    border: `1px solid ${T.border}`,
                    overflow: "hidden",
                    background: T.surface,
                  }}
                >
                  {open && film.embed !== null ? (
                    <iframe
                      src={film.embed}
                      title={title}
                      /* Focused as it arrives, because the control that was in the tab order a
                         moment ago has just been replaced by it — without this a keyboard user's
                         focus falls back to the top of the document. */
                      ref={(node) => {
                        node?.focus();
                      }}
                      loading="lazy"
                      referrerPolicy="strict-origin-when-cross-origin"
                      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
                      allowFullScreen
                      style={{ width: "100%", height: "100%", border: "none", display: "block" }}
                    />
                  ) : (
                    <>
                      <ImagePlate
                        seed={`video-${film.slot}`}
                        src={film.poster}
                        alt=""
                        style={{
                          width: "100%",
                          height: "100%",
                          minHeight: 0,
                          border: "none",
                          borderRadius: 0,
                        }}
                      />
                      {playable ? (
                        <button
                          type="button"
                          onClick={() => setPlaying(film.slot)}
                          aria-label={`${playLabel}, ${title}`}
                          style={{
                            position: "absolute",
                            inset: 0,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            gap: step(0.4),
                            background: "transparent",
                            border: "none",
                            cursor: "pointer",
                            padding: 0,
                          }}
                        >
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: step(0.4),
                              background: T.accent,
                              color: T.accentText,
                              fontFamily: T.fontBody,
                              fontSize: "0.9375rem",
                              fontWeight: 700,
                              lineHeight: 1,
                              padding: `${step(0.7)} ${step(1.1)}`,
                              borderRadius: T.radius,
                            }}
                          >
                            <span aria-hidden>▶</span>
                            {playLabel}
                          </span>
                        </button>
                      ) : null}
                      {/* No play control where there is no embed address: the poster is a still,
                          and the way out to the host is the named link under it. An overlay link
                          there would be a second control to the same place with a worse name. */}
                    </>
                  )}
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: step(0.2) }}>
                  {film.meta ? (
                    <span
                      style={{
                        fontFamily: T.fontBody,
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        letterSpacing: "0.06em",
                        textTransform: "uppercase",
                        color: T.muted,
                      }}
                    >
                      {film.meta}
                    </span>
                  ) : null}
                  <StoreHeading level={3} size="sm" style={{ overflowWrap: "anywhere" }}>
                    {title}
                  </StoreHeading>
                  {/* `isSafeHref`, not a null check — a refused destination is non-null, and a
                      watch link that cannot be followed is worse than no watch link. */}
                  {isSafeHref(film.href ?? "") && watchLabel !== "" ? (
                    <TileShell
                      href={film.href}
                      style={{
                        fontFamily: T.fontBody,
                        fontSize: "0.8125rem",
                        fontWeight: 700,
                        color: T.accent,
                        textDecoration: "underline",
                        alignSelf: "flex-start",
                      }}
                    >
                      {/* The wording alone would be four identical links on a Videos page, so each
                          one carries the film's title in its accessible name. The visible words are
                          hidden from the name rather than removed, so they still read on screen. */}
                      <span aria-hidden>{watchLabel}</span>
                      <VisuallyHidden>{`${watchLabel}, ${title}`}</VisuallyHidden>
                    </TileShell>
                  ) : null}
                  {playable && privacyNote ? (
                    <StoreText muted small style={{ maxWidth: "none", marginTop: step(0.15) }}>
                      {privacyNote}
                    </StoreText>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

