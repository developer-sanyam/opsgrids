/**
 * The three primitive options added for sections that asked for them.
 *
 * Each exists because a section hit a wall the primitives owned: a wordmark that could not get
 * bigger than a section title, off-screen text that could not be written without a literal `1px`
 * inside `render/`, and an outline action on a scrim taking the page's text colour, which is the
 * wrong end of the pair on a dark photograph.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ActionLink, StoreHeading, VisuallyHidden } from "./primitives";

describe("the display heading step", () => {
  it("is a step above xl, not a repeat of it", () => {
    render(
      <>
        <StoreHeading size="display">Wordmark</StoreHeading>
        <StoreHeading size="xl">Section title</StoreHeading>
      </>,
    );

    const display = screen.getByRole("heading", { name: "Wordmark" });
    const xl = screen.getByRole("heading", { name: "Section title" });

    expect(display.style.fontSize).not.toBe(xl.style.fontSize);
    // jsdom has no layout engine, so the floor of the clamp is what can be checked: the point of
    // the step is that a phone, where the `vw` term loses, still gets a wordmark and not a title.
    expect(display.style.fontSize.startsWith("clamp(2.75rem")).toBe(true);
  });
});

describe("a heading's fluid ramp", () => {
  /*
   * `vw` is the browser window; a storefront canvas is a fixed-width box. Handed the canvas, the
   * ramp measures that instead — which is the only way the editor's 390px preview can tell a
   * merchant the truth about their phone layout while it sits inside a 1600px console window.
   */
  it("is the canvas, not the window, when the canvas is known", () => {
    render(
      <>
        <StoreHeading width={1280}>Desktop</StoreHeading>
        <StoreHeading width={390}>Phone</StoreHeading>
      </>,
    );

    // 1280 × 0.026 is what `2.6vw` resolved to in a full-width desktop window: unmoved.
    expect(screen.getByRole("heading", { name: "Desktop" }).style.fontSize).toBe(
      "clamp(1.5rem, 33.3px, 2.1rem)",
    );
    // 390 × 0.026 is 10.1px, so the floor wins — a phone heading, which is the whole point.
    expect(screen.getByRole("heading", { name: "Phone" }).style.fontSize).toBe(
      "clamp(1.5rem, 10.1px, 2.1rem)",
    );
  });

  it("keeps the old viewport ramp for a caller that has not been threaded yet", () => {
    render(<StoreHeading>Untreaded</StoreHeading>);

    expect(screen.getByRole("heading", { name: "Untreaded" }).style.fontSize).toBe(
      "clamp(1.5rem, 2.6vw, 2.1rem)",
    );
  });

  it("leaves the two fixed steps alone — a caption does not grow with the page", () => {
    render(
      <>
        <StoreHeading size="md" width={390}>
          Medium
        </StoreHeading>
        <StoreHeading size="sm" width={1280}>
          Small
        </StoreHeading>
      </>,
    );

    expect(screen.getByRole("heading", { name: "Medium" }).style.fontSize).toBe("1.25rem");
    expect(screen.getByRole("heading", { name: "Small" }).style.fontSize).toBe("1rem");
  });
});

describe("visually hidden text", () => {
  it("is in the document and reachable by name", () => {
    render(<VisuallyHidden>Skip to the catalogue</VisuallyHidden>);

    expect(screen.getByText("Skip to the catalogue")).not.toBeNull();
  });

  it("is a live region when asked, and is present before it has anything to say", () => {
    const { container } = render(<VisuallyHidden live="polite">{""}</VisuallyHidden>);

    const region = container.querySelector("[aria-live='polite']");
    expect(region).not.toBeNull();
    expect(region?.textContent).toBe("");
  });
});

describe("an action on a scrim", () => {
  it("takes the page's own text colour by default", () => {
    render(
      <MemoryRouter>
        <ActionLink href="/store/catalogue" variant="outline">
          Shop now
        </ActionLink>
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "Shop now" });
    expect(link.style.color).toBe("var(--st-text)");
    expect(link.style.border).toContain("var(--st-border)");
  });

  it("takes the surrounding foreground for both its words and its border when told to", () => {
    render(
      <MemoryRouter>
        <ActionLink href="/store/catalogue" variant="outline" tone="inherit">
          Shop now
        </ActionLink>
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "Shop now" });
    expect(link.style.color).toBe("inherit");
    // The CSSOM lower-cases the keyword; what matters is that the border is the foreground and not
    // the theme's border token.
    expect(link.style.border.toLowerCase()).toContain("currentcolor");
  });
});
