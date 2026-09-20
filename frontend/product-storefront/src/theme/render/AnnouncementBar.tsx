import { useId, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useLineList } from "@/store/lineList";
import { BLEED_SETTING, STACK_WIDTH } from "../schema";
import { isExternalHref } from "./linkList";
import { pageGutter } from "./pageGrid";
import { ActionButton, ActionLink } from "./primitives";
import { bool, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * Above this canvas width the strip is one row: promise and address left, search in the middle,
 * account and basket right. Below it the search drops to a second row full width, which is what
 * the reference does and the only way a real search box fits beside an address at 390.
 *
 * A number here rather than a media query, for the same reason `stacked` is one: the editor's
 * device preview and a genuinely narrow window have to agree, and a media query would measure the
 * console's window instead of the canvas.
 */
const SEARCH_INLINE_WIDTH = 760;

/** Below this the address drops its label — "Sakinaka…" rather than "Deliver to · Sakinaka…". */
const TIGHT_WIDTH = 420;

/**
 * The count comes from the in-page line list — the one context a section may read, and only through
 * `useLineList()` (see the amended rule at the top of `render/types.ts`). Outside a
 * `LineListProvider` the hook hands back an empty list, so a miniature or the editor's canvas shows
 * a zero that is honestly zero rather than a number belonging to some other store.
 *
 * Still text and still not a link. The list lives in this page and nothing else: there is no basket
 * route to send anyone to, and a control that looks like a destination and goes nowhere costs a
 * buyer more than the control is worth.
 */

/** A magnifier drawn from `currentColor`, so it takes the button's own foreground in every theme. */
function SearchGlyph() {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden
      focusable="false"
      style={{ width: step(0.8), height: step(0.8), display: "block" }}
    >
      <circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <line
        x1="10.6"
        y1="10.6"
        x2="14.4"
        y2="14.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * The strip across the top of a retail store: the delivery promise, the address it applies to, a
 * search box, an account link and the basket.
 *
 * Three decisions worth knowing about:
 *
 * - **It is a `<section aria-label>`, not a `<header>`.** The store chrome already renders one
 *   banner landmark. Two is not a styling nit — it makes "jump to the banner" ambiguous, which is
 *   the one thing landmarks exist to prevent.
 * - **The search is a real `<form role="search">`** with a named input that navigates on submit. A
 *   div dressed as a field is worse than no field: a buyer types into it, presses enter, and
 *   nothing happens.
 * - **The promise is the merchant's own sentence**, from `promiseText`, never derived from
 *   catalogue data. A per-line delivery estimate and a store-wide promise are different facts —
 *   one is what this parcel does, the other is what the store claims — and `Product` splits them
 *   apart precisely so that a section cannot quietly merge them back together.
 */
export function AnnouncementBar({ settings, width, mode }: SectionRenderProps) {
  const navigate = useNavigate();
  const searchId = useId();
  const [query, setQuery] = useState("");

  const promise = optional(settings, "promiseText");
  const locationLabel = optional(settings, "locationLabel");
  const locationText = optional(settings, "locationText");
  const showSearch = bool(settings, "showSearch", true);
  const searchLabel = str(settings, "searchLabel", "Search the catalogue");
  const searchPlaceholder = str(settings, "searchPlaceholder", "");
  const searchHref = str(settings, "searchHref", "/store/catalogue");
  const accountLabel = optional(settings, "accountLabel");
  const accountHref = str(settings, "accountHref", "/store/contact");
  const showCart = bool(settings, "showCart", true);
  const tint = optional(settings, "tint");
  const bleeding = bool(settings, BLEED_SETTING, true);

  const inlineSearch = width >= SEARCH_INLINE_WIDTH;
  const tight = width < TIGHT_WIDTH;

  // An empty label means "use the wording that matches the store", which is the only thing mode
  // decides here: a trade buyer builds a line list, a retail buyer fills a basket.
  const cartLabel = optional(settings, "cartLabel") ?? (mode === "retail" ? "My cart" : "Line list");
  const countNoun = mode === "retail" ? "item" : "line";

  /*
   * The two vocabularies count different things, and reporting one number under both nouns would
   * be wrong in whichever store it was not written for. Three packets of butter is "3 items" and
   * one line; a trade line at MOQ 500 is "1 line" and emphatically not "500 items".
   */
  const list = useLineList();
  const count = mode === "retail" ? list.units : list.count;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const typed = query.trim();
    const target =
      typed === ""
        ? searchHref
        : `${searchHref}${searchHref.includes("?") ? "&" : "?"}q=${encodeURIComponent(typed)}`;
    // A merchant may point search at another site; the router cannot take a buyer there.
    if (isExternalHref(target)) {
      window.location.assign(target);
      return;
    }
    navigate(target);
  }

  const searchForm = showSearch ? (
    <form
      role="search"
      aria-label={searchLabel}
      onSubmit={submitSearch}
      style={{
        display: "flex",
        alignItems: "stretch",
        gap: step(0.4),
        flex: "1 1 auto",
        minWidth: 0,
        ...(inlineSearch ? { maxWidth: step(30) } : {}),
      }}
    >
      <input
        id={searchId}
        name="q"
        type="search"
        aria-label={searchLabel}
        placeholder={searchPlaceholder}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        style={{
          flex: "1 1 auto",
          minWidth: 0,
          fontFamily: T.fontBody,
          fontSize: "0.875rem",
          color: T.text,
          background: T.bg,
          border: `1px solid ${T.border}`,
          borderRadius: T.radius,
          padding: `${step(0.5)} ${step(0.65)}`,
        }}
      />
      <ActionButton size="sm" ariaLabel={searchLabel}>
        <SearchGlyph />
      </ActionButton>
    </form>
  ) : null;

  const promiseBlock =
    promise || locationText ? (
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: "1 1 auto" }}>
        {promise ? (
          <span
            style={{
              fontFamily: T.fontHeading,
              fontSize: "0.9375rem",
              fontWeight: 800,
              color: T.text,
              lineHeight: 1.25,
            }}
          >
            {promise}
          </span>
        ) : null}
        {locationText ? (
          <span
            style={{
              fontFamily: T.fontBody,
              fontSize: "0.75rem",
              color: T.muted,
              lineHeight: 1.35,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: "100%",
            }}
          >
            {locationLabel && !tight ? `${locationLabel} · ` : null}
            {locationText}
          </span>
        ) : null}
      </div>
    ) : null;

  const actions =
    accountLabel || showCart ? (
      <div style={{ display: "flex", alignItems: "center", gap: step(0.6), flex: "0 0 auto" }}>
        {accountLabel ? (
          <ActionLink href={accountHref} variant="outline" size="sm">
            {accountLabel}
          </ActionLink>
        ) : null}
        {showCart ? (
          /*
           * Text, not a link. There is no basket page on this build, and a control that looks like
           * a destination and goes nowhere costs a buyer more than the control is worth.
           * `aria-live` is here already so the count can start changing without the markup moving.
           */
          <p
            aria-live="polite"
            style={{
              margin: 0,
              display: "flex",
              alignItems: "baseline",
              gap: step(0.3),
              fontFamily: T.fontBody,
              fontSize: "0.8125rem",
              fontWeight: 700,
              color: T.text,
              whiteSpace: "nowrap",
            }}
          >
            <span>{cartLabel}</span>
            <span style={{ color: T.muted, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
              {count} {count === 1 ? countNoun : `${countNoun}s`}
            </span>
          </p>
        ) : null}
      </div>
    ) : null;

  return (
    <section
      aria-label="Delivery and search"
      style={{
        background: tint ?? T.surface,
        border: `1px solid ${T.border}`,
        ...(bleeding ? {} : { borderRadius: T.radius }),
        boxSizing: "border-box",
        paddingBlock: step(0.7),
        paddingInline: bleeding ? pageGutter(width < STACK_WIDTH) : step(1),
        height: "100%",
        display: "flex",
        alignItems: "center",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: T.maxWidth,
          marginInline: "auto",
          minWidth: 0,
          display: "flex",
          /*
           * One row when the search fits beside everything else, two when it does not: promise and
           * address opposite the account and the basket, then the search full width underneath —
           * which is what the reference does at phone width.
           *
           * Wrapping a single flex line instead produces *three* rows at 390, because flex wraps on
           * the items' base widths before it shrinks any of them and the address is wide. Found in
           * Chrome, not reasoned about.
           */
          flexDirection: inlineSearch ? "row" : "column",
          alignItems: inlineSearch ? "center" : "stretch",
          gap: step(inlineSearch ? 1 : 0.55),
        }}
      >
        {inlineSearch ? (
          <>
            {promiseBlock}
            {searchForm}
            {actions}
          </>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: step(0.6),
                minWidth: 0,
              }}
            >
              {promiseBlock}
              {actions}
            </div>
            {searchForm}
          </>
        )}
      </div>
    </section>
  );
}
