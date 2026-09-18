/**
 * The gallery miniature's containment.
 *
 * **This is a structural test, not a hit test, and the difference matters.** jsdom performs no
 * layout: it has no viewport, `getBoundingClientRect` returns zeroes, `elementFromPoint` is not
 * usefully implemented, and Testing Library's `click` dispatches straight at the node it is given
 * without ever asking what is on top. So nothing here — and nothing that *can* be written here —
 * would catch "a click at the button's coordinates landed on something else". That verification
 * has to happen in a real browser, and it did: a CDP `Input.dispatchMouseEvent` at the adopt
 * button's centre hit the button and produced the confirm step.
 *
 * What this file does pin is the three properties that make interception impossible in the first
 * place, each of which is a one-line deletion away from being lost:
 *
 *   1. the frame clips (`overflow: hidden` + an explicit height) — the canvas inside is composed at
 *      1280px and is thousands of pixels tall before `transform: scale` shrinks it *visually only*;
 *   2. the scaled canvas takes no pointer events;
 *   3. the frame is `inert`, so the store page's links and form fields inside it are not tab stops.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { depot } from "@/theme/themes";
import { ThemePreview } from "./ThemePreview";

function renderPreview() {
  return render(
    <MemoryRouter>
      <ThemePreview theme={depot} products={PRODUCTS} categories={CATEGORY_LIST} />
    </MemoryRouter>,
  );
}

describe("the miniature cannot escape its frame", () => {
  it("clips: the frame has a fixed height and hides its overflow", () => {
    renderPreview();
    const frame = screen.getByTestId("theme-preview-frame");

    expect(frame.className).toContain("overflow-hidden");
    expect(frame.style.height).toBe("232px");
  });

  it("composes the canvas wider than the card and relies on the clip, not on the transform", () => {
    renderPreview();
    const canvas = screen.getByTestId("theme-preview-canvas");

    // 1280px wide inside a ~400px card: without the clip above, this would cover the card.
    expect(canvas.style.width).toBe("1280px");
    expect(canvas.style.transform).toMatch(/^scale\(/);
    expect(canvas.style.transformOrigin).toBe("top left");
  });

  it("takes no pointer events on the scaled canvas", () => {
    renderPreview();
    expect(screen.getByTestId("theme-preview-canvas").style.pointerEvents).toBe("none");
  });
});

describe("the miniature is not a set of tab stops", () => {
  it("is inert, so the store page inside it is neither focusable nor hit-testable", () => {
    renderPreview();
    expect(screen.getByTestId("theme-preview-frame").hasAttribute("inert")).toBe(true);
  });

  it("really does contain focusable content — which is why inert is required, not optional", () => {
    renderPreview();
    const frame = screen.getByTestId("theme-preview-frame");

    // The Depot home page carries an enquiry form and navigation links. `aria-hidden` alone would
    // hide these from a screen reader while leaving every one of them tabbable.
    const focusable = frame.querySelectorAll("a[href], button, input, textarea, [tabindex]");
    expect(focusable.length).toBeGreaterThan(0);
    expect(frame.getAttribute("aria-hidden")).toBe("true");
  });
});
