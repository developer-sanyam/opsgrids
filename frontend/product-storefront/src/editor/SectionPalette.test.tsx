import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PageDoc, SectionDef } from "@/theme/schema";

import { SectionPalette } from "./SectionPalette";

/**
 * The outline's order controls. A keyboard moves a section with Alt and an arrow; a finger has no
 * Alt, so on a touch device every row carries Move up, Move down and Remove buttons — and the copy
 * that names a key chord is replaced by copy that names the buttons.
 */

const BLOCK: SectionDef = {
  type: "test-block",
  name: "Test block",
  group: "content",
  defaultLayout: { w: 6, h: 4 },
  fields: [],
};

const PAGE: PageDoc = {
  id: "home",
  route: "/",
  name: "Home",
  sections: [
    { id: "a", type: "test-block", settings: {}, layout: { x: 0, y: 0, w: 6, h: 4 } },
    { id: "b", type: "test-block", settings: {}, layout: { x: 0, y: 4, w: 6, h: 4 } },
  ],
};

function touchDevice(coarse: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: coarse && query === "(pointer: coarse)",
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

function renderPalette(canEdit = true) {
  const handlers = { onAdd: vi.fn(), onSelect: vi.fn(), onDepth: vi.fn(), onRemove: vi.fn() };
  render(
    <SectionPalette
      library={[BLOCK]}
      page={PAGE}
      selectedId={null}
      canEdit={canEdit}
      {...handlers}
    />,
  );
  return handlers;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the outline on a touch device", () => {
  it("gives each row Move up, Move down and Remove, and sends them to the right section", () => {
    touchDevice(true);
    const { onDepth, onRemove } = renderPalette();

    // Two rows, so two of each; the first row cannot go up and the last cannot go down.
    const ups = screen.getAllByRole("button", { name: "Move Test block up" });
    const downs = screen.getAllByRole("button", { name: "Move Test block down" });
    expect(ups).toHaveLength(2);
    expect(downs).toHaveLength(2);
    expect((ups[0] as HTMLButtonElement).disabled).toBe(true);
    expect((downs[1] as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(downs[0] as HTMLElement);
    expect(onDepth).toHaveBeenLastCalledWith("a", "forward");
    fireEvent.click(ups[1] as HTMLElement);
    expect(onDepth).toHaveBeenLastCalledWith("b", "backward");

    fireEvent.click(screen.getAllByRole("button", { name: "Remove Test block" })[1] as HTMLElement);
    expect(onRemove).toHaveBeenCalledWith("b");
  });

  it("makes every one a 44px target and drops the Alt-key instruction", () => {
    touchDevice(true);
    renderPalette();
    for (const button of screen.getAllByRole("button", { name: /^(Move|Remove) Test block/ })) {
      expect(button.className).toContain("size-11");
    }
    expect(screen.queryByText(/Alt with up or down/)).toBeNull();
    expect(screen.getByText(/Use Move up and Move down/)).toBeTruthy();
  });

  it("offers no order buttons to someone who cannot edit", () => {
    touchDevice(true);
    renderPalette(false);
    expect(screen.queryByRole("button", { name: /^Move / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
  });
});

describe("the outline with a mouse", () => {
  it("keeps the Alt-arrow instruction and adds no extra buttons", () => {
    touchDevice(false);
    renderPalette();
    expect(screen.getByText(/Alt with up or down/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Move / })).toBeNull();
  });
});
