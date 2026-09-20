import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";

import { formatMinor } from "@/lib/format";
import {
  RETRY_LABEL,
  UNKNOWN_HEADLINE,
  UNKNOWN_NOTE,
  postalCodeError,
  readStoredPostalCode,
  servedHeadline,
  unservedHeadline,
  unservedNote,
  useServiceability,
} from "@/lib/serviceability";
import type { ServiceabilityOut } from "@/lib/serviceability";
import { BLEED_SETTING, STACK_WIDTH } from "../schema";
import { pageGutter } from "./pageGrid";
import { ActionButton, SectionEmpty, StoreField, VisuallyHidden } from "./primitives";
import { bool, optional, str } from "./settings";
import type { Settings } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/**
 * The buyer's serviceability check: they type a postal code, the store answers.
 *
 * **Three outcomes, and the third is the one that matters.** Served, not served, and *couldn't
 * check* — and "couldn't check" is a first-class state with its own copy and its own button, never
 * an error toast and emphatically never rendered as "we don't deliver here" (ADR-0010 §13). The
 * client computes none of them: it renders the verdict the server sent (`serviceable`), the reason
 * behind a refusal and the promise it resolved, and a failure of any kind lands on the third.
 * Everything that decides which is which lives in `lib/serviceability.ts`, so this file is markup
 * and layout and nothing else.
 *
 * **The date is the server's.** A `DATE_OFFSET` promise arrives as a calendar date already
 * resolved in the *store's* timezone, and it is rendered with its parts read out of the string —
 * never handed to a formatter that would re-read it in the buyer's zone and print yesterday.
 *
 * **Retail only.** A trade store's buyer builds a line list and enquires; there is no doorstep
 * promise to make and a delivery bar on a wholesaler's page would be claiming one. The guard is
 * not cosmetic — it stops the section asking the server a question about a store whose answer it
 * would then display in a context where it means something else.
 *
 * Three layout decisions worth knowing:
 *
 * - **The answer region reserves its height from the first paint.** The control is at the top of
 *   the page, so a region that grows when the answer lands would push the whole store down under
 *   the buyer's thumb. It holds the tallest of the four states at every width.
 * - **`width`, never a media query.** The editor's phone preview measures the canvas, not the
 *   console window, so the two agree.
 * - **One live region, present before anything happens.** A `role="status"` that only appears once
 *   there is something to say is a region the screen reader was not watching when it changed.
 */

/** Above this canvas width the form and the answer sit side by side. Below, they stack. */
const INLINE_WIDTH = 720;

/**
 * The height of a small `ActionButton`, written out: two lots of its vertical padding, its own
 * type, and its one-pixel border top and bottom.
 *
 * The headline row reserves it whether or not the retry button is in that row, so "couldn't check"
 * is exactly as tall as the other three states. Computing it rather than picking a round number is
 * what makes that true in *every* theme: the padding is a multiple of the theme's spacing step, so
 * a fixed reserve that holds at QuickCart's step of 12 is seven pixels short at Atelier's 20 — and
 * seven pixels is a page that jumps.
 */
const ACTION_HEIGHT = `calc(${step(0.9)} + 0.8125rem + 2px)`;

/** One line of `Detail`: its type times its line height. */
const DETAIL_LINE = "1.1rem";

/** Where the merchant's own promise sits until the buyer has a real answer (ADR-0010 §15). */
function idleLine(settings: Settings): string | null {
  return optional(settings, "idlePromiseText");
}

/**
 * "₹29 delivery · free over ₹499", or `null`.
 *
 * **Display values only.** These are the zone's stored amounts; when a cart exists its pricing
 * engine owns the total and this stops being authoritative (ADR-0010 §8). A free delivery is said
 * in words rather than shown as "₹0", which reads as a missing value.
 *
 * The amounts are a nested `FeesOut` and the currency belongs to it — there is no top-level
 * currency on the answer, so there is nothing here to fall back to a hard-coded one.
 */
function feeLine(answer: ServiceabilityOut): string | null {
  const fees = answer.fees ?? null;
  if (fees === null) return null;
  /* `min_order_minor` carries a server-side default of 0 and may be absent from the body. */
  const minimum = fees.min_order_minor ?? 0;
  const freeAbove = fees.free_delivery_above_minor ?? null;
  const parts: string[] = [
    fees.delivery_fee_minor === 0
      ? "Free delivery"
      : `${formatMinor(fees.delivery_fee_minor, fees.currency)} delivery`,
  ];
  if (freeAbove !== null && fees.delivery_fee_minor !== 0) {
    parts.push(`free over ${formatMinor(freeAbove, fees.currency)}`);
  }
  if (minimum > 0) parts.push(`${formatMinor(minimum, fees.currency)} minimum`);
  return parts.join(" · ");
}

function Headline({ children }: { children: string }) {
  return (
    <span
      style={{
        fontFamily: T.fontHeading,
        fontSize: "0.9375rem",
        fontWeight: 800,
        color: T.text,
        lineHeight: 1.25,
      }}
    >
      {children}
    </span>
  );
}

function Detail({ children }: { children: string }) {
  return (
    <span style={{ fontFamily: T.fontBody, fontSize: "0.8125rem", color: T.muted, lineHeight: 1.35 }}>
      {children}
    </span>
  );
}

export function DeliveryCheck({ settings, width, mode }: SectionRenderProps) {
  const base = useId();
  const fieldId = `${base}-pincode`;

  const slug = optional(settings, "storeSlug");
  /* Nothing is asked of the server unless this store is a retail store *and* the merchant has said
     which store to ask about. An unset slug reads storage as `""`, which is never a key. */
  const usable = mode === "retail" && slug !== null;
  const activeSlug = usable && slug !== null ? slug : "";

  const { state, check, retry } = useServiceability(activeSlug);
  /* Prefilled from the buyer's own last answer — the code is stored, the answer never is (§6), so
     the field is populated and the check re-runs rather than the verdict being remembered. */
  const [typed, setTyped] = useState(() => readStoredPostalCode(activeSlug) ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const root = useRef<HTMLElement | null>(null);

  /*
   * The re-check on load (ADR-0010 §5): a stored answer is never trusted, so a stored *code* is
   * asked about again on every page view.
   *
   * **Unless nobody is looking at it.** The gallery draws a miniature of every theme, each a whole
   * store page inside an `inert` subtree; without this guard a merchant opening the gallery would
   * post one check per miniature, and each would land in their own demand report as a postal code
   * no buyer typed. `closest("[inert]")` is a DOM read of this section's own ancestry — not a
   * second context, and not something a section can get wrong about itself.
   */
  useEffect(() => {
    if (!usable) return;
    if (root.current?.closest("[inert]") != null) return;
    const stored = readStoredPostalCode(activeSlug);
    if (stored === null) return;
    check(stored);
  }, [activeSlug, check, usable]);

  const tint = optional(settings, "tint");
  const bleeding = bool(settings, BLEED_SETTING, true);
  const stacked = width < STACK_WIDTH;
  const inline = width >= INLINE_WIDTH;
  const checking = state.kind === "checking";

  const shell: CSSProperties = {
    background: tint ?? T.surface,
    border: `1px solid ${T.border}`,
    ...(bleeding ? {} : { borderRadius: T.radius }),
    boxSizing: "border-box",
    paddingBlock: step(0.7),
    paddingInline: bleeding ? pageGutter(stacked) : step(1),
    height: "100%",
    display: "flex",
    alignItems: "center",
  };

  if (!usable) {
    /*
     * Two different absences, one treatment, because the remedy is the same sentence in the
     * settings panel. `SectionEmpty` is the library's own "this block has nothing to draw" state,
     * and it is what every other section shows rather than a hole in the grid — a hole is what a
     * merchant reports as "the editor deleted my block".
     */
    return (
      <section aria-label="Delivery check" style={shell}>
        <div style={{ width: "100%", maxWidth: T.maxWidth, marginInline: "auto" }}>
          <SectionEmpty
            title="Delivery check"
            hint={
              mode === "retail"
                ? "Add the store handle in the settings panel — the check has to name a store when it asks."
                : "This block is for retail stores. A trade store takes enquiries rather than promising a delivery time, so nothing is shown here."
            }
          />
        </div>
      </section>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // The second press while one is in flight is ignored, so a buyer cannot count themselves twice
    // into the merchant's demand report.
    if (checking) return;
    const problem = postalCodeError(typed);
    setFieldError(problem);
    // A shape the server would refuse never reaches the network — and what was typed stays put.
    if (problem !== null) return;
    check(typed);
  }

  const form = (
    <form
      onSubmit={onSubmit}
      noValidate
      aria-label="Check delivery to your area"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: step(0.4),
        flex: inline ? "0 0 auto" : "1 1 auto",
        minWidth: 0,
        ...(inline ? { width: step(22) } : {}),
      }}
    >
      <StoreField
        id={fieldId}
        name="postal_code"
        label={str(settings, "fieldLabel", "PIN code")}
        hideLabel
        value={typed}
        onChange={(next) => {
          setTyped(next);
          // The complaint goes as soon as they start fixing it. Re-validating on every keystroke
          // instead would tell somebody typing "4" that four is not six digits.
          setFieldError(null);
        }}
        {...(fieldError === null ? {} : { error: fieldError })}
        placeholder={str(settings, "placeholder", "400072")}
        inputMode="numeric"
        autoComplete="postal-code"
        maxLength={10}
        reserveError
        style={{ flex: "1 1 auto" }}
      />
      <ActionButton size="sm" disabled={checking}>
        {checking ? "Checking…" : str(settings, "submitLabel", "Check")}
      </ActionButton>
    </form>
  );

  /*
   * Every state is one headline and one detail, and never a third row.
   *
   * That is what keeps the bar a single height: the retry button rides *beside* the headline
   * rather than under it, and a zone's `buyer_note` joins the detail rather than starting a line
   * of its own. A state that is one row taller than its neighbours is a page that jumps when the
   * answer arrives, and this control sits above everything a buyer is reading.
   */
  const headline =
    state.kind === "served"
      ? state.promise ?? servedHeadline(state.postalCode)
      : state.kind === "unserved"
        ? unservedHeadline(state.postalCode, state.reason)
        : state.kind === "unknown"
          ? UNKNOWN_HEADLINE
          : idleLine(settings);

  const detail = (() => {
    if (state.kind === "checking") return `Checking ${state.postalCode}…`;
    if (state.kind === "unknown") return UNKNOWN_NOTE;
    // The merchant's own words about their own area, when the zone carries any.
    if (state.kind === "unserved") return state.answer.buyer_note ?? unservedNote(state.reason);
    if (state.kind === "served") {
      /*
       * With a promise, the headline *is* the promise and this line names the area. Without one,
       * the headline names the area and this line says why there is no promise — and only
       * `SLOTTED` has an answer to that. A `PROMISED` zone whose promise fields came back empty
       * gets neither sentence: "windows are shown at checkout" would be a claim about a checkout
       * that will not offer any. `mode` is what tells those two apart, which is the second
       * question and the only one it can answer.
       */
      const lead =
        state.promise !== null
          ? servedHeadline(state.postalCode)
          : state.mode === "SLOTTED"
            ? "Delivery windows are shown at checkout."
            : null;
      const parts = [lead, feeLine(state.answer), state.answer.buyer_note ?? null].filter(
        (part): part is string => part !== null && part !== "",
      );
      return parts.join(" · ");
    }
    return str(settings, "promptText", "Check your PIN code for the promise where you are.");
  })();

  const answer = (
    /*
     * One region, always mounted, with its height reserved. `role="status"` is an implicit polite
     * live region; the height is what stops the store below jumping when the answer lands. The
     * reserve is in `rem` because what it holds is two rows of *type* — a spacing-token multiple
     * would be a different number of lines in each theme, which is how one theme ends up jumping.
     */
    <div
      role="status"
      aria-live="polite"
      aria-busy={checking}
      style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: step(0.15),
        flex: "1 1 auto",
        minWidth: 0,
        /* One headline row plus one or two lines of detail, held from the first paint. Two when
           stacked, because at 390 every answer sentence wraps. */
        minHeight: `calc(${ACTION_HEIGHT} + ${step(0.15)} + ${inline ? DETAIL_LINE : `2 * ${DETAIL_LINE}`})`,
      }}
    >
      {headline === null ? null : (
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: step(0.6),
            flexWrap: "wrap",
            minHeight: ACTION_HEIGHT,
          }}
        >
          <Headline>{headline}</Headline>
          {state.kind === "unknown" ? (
            <ActionButton type="button" variant="outline" size="sm" onClick={retry}>
              {RETRY_LABEL}
            </ActionButton>
          ) : null}
        </span>
      )}
      <Detail>{detail}</Detail>
      {/* Never "we don't deliver here". The visible words say what happened and the button is the
          remedy; this line spells the distinction out for a screen reader, where the visual
          difference between "couldn't check" and a refusal does not exist. */}
      {state.kind === "unknown" ? (
        <VisuallyHidden>This is not an answer about whether we deliver to you.</VisuallyHidden>
      ) : null}
    </div>
  );

  return (
    <section ref={root} aria-label="Delivery check" style={shell}>
      <div
        style={{
          width: "100%",
          maxWidth: T.maxWidth,
          marginInline: "auto",
          minWidth: 0,
          display: "flex",
          flexDirection: inline ? "row" : "column",
          alignItems: inline ? "center" : "stretch",
          gap: step(inline ? 1.2 : 0.5),
        }}
      >
        {form}
        {answer}
      </div>
    </section>
  );
}
