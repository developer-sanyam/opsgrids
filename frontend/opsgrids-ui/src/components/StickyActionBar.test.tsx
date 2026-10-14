import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stubViewport } from "../test-media";
import { StickyActionBar } from "./StickyActionBar";

afterEach(() => vi.unstubAllGlobals());

describe("StickyActionBar", () => {
  it("pins the action above the bottom navigation on a phone and marks the page for scroll-padding", () => {
    stubViewport(390);
    render(
      <StickyActionBar summary={<span>Total</span>}>
        <button type="button">Review</button>
      </StickyActionBar>,
    );
    const bar = screen.getByRole("button", { name: "Review" }).closest("[data-action-bar]");
    expect(bar).not.toBeNull();
    expect(bar?.classList.contains("shell-sticky-action")).toBe(true);
    expect(bar?.contains(screen.getByText("Total"))).toBe(true);
  });

  it("renders nothing from md up, so the desktop control is not in the document twice", () => {
    stubViewport(1024);
    render(
      <StickyActionBar>
        <button type="button">Review</button>
      </StickyActionBar>,
    );
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  });

  it("renders at every width when always is set", () => {
    stubViewport(1024);
    render(
      <StickyActionBar always bleed={false}>
        <button type="button">Submit</button>
      </StickyActionBar>,
    );
    const bar = screen.getByRole("button", { name: "Submit" }).closest("[data-action-bar]");
    expect(bar).not.toBeNull();
    expect(bar?.classList.contains("-mx-3")).toBe(false);
  });
});
