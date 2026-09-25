/**
 * The menu button pattern, whole: how it opens, how focus moves inside, how it closes, and where
 * focus goes when an item is chosen — which is what decides whether a dialog the item opens can
 * give focus back to something that still exists.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Menu } from "./Menu";
import type { MenuItem } from "./Menu";

function items(overrides: Partial<Record<string, () => void>> = {}): MenuItem[] {
  return [
    { label: "Export CSV", onSelect: overrides["csv"] ?? vi.fn() },
    { label: "Export PDF", onSelect: overrides["pdf"] ?? vi.fn() },
    { label: "Schedule report", onSelect: vi.fn() },
    { label: "Share", onSelect: vi.fn(), disabled: true, disabledReason: "Reports can be shared once saved." },
    { label: "Suspend account", onSelect: vi.fn(), hidden: true },
  ];
}

describe("Menu", () => {
  it("opens on the first item, marked expanded, and leaves hidden items out", async () => {
    const user = userEvent.setup();
    render(<Menu label="Export" items={items()} />);
    const trigger = screen.getByRole("button", { name: "Export" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("menu", { name: "Export" })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Export CSV" }));
    expect(screen.queryByRole("menuitem", { name: "Suspend account" })).toBeNull();
  });

  it("opens on the last item with Up from the trigger", async () => {
    const user = userEvent.setup();
    render(<Menu label="Export" items={items()} />);
    await user.tab();
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement?.textContent).toContain("Share");
  });

  it("moves with the arrows, wraps, and jumps with Home and End", async () => {
    const user = userEvent.setup();
    render(<Menu label="Export" items={items()} />);
    await user.tab();
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Export PDF" }));
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(document.activeElement?.textContent).toContain("Share");
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Export CSV" }));
    await user.keyboard("{End}");
    expect(document.activeElement?.textContent).toContain("Share");
  });

  it("jumps to an item by typing the start of its label", async () => {
    const user = userEvent.setup();
    render(<Menu label="Export" items={items()} />);
    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.keyboard("sc");
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Schedule report" }));
  });

  it("closes on Escape and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    render(<Menu label="Export" items={items()} />);
    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Export" }));
  });

  it("returns focus to the trigger before the chosen action runs", async () => {
    const user = userEvent.setup();
    let focusedWhenRun: Element | null = null;
    render(
      <Menu
        label="Export"
        items={items({
          pdf: () => {
            focusedWhenRun = document.activeElement;
          },
        })}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(screen.getByRole("menuitem", { name: "Export PDF" }));
    expect(focusedWhenRun).toBe(screen.getByRole("button", { name: "Export" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("keeps a disabled item focusable, reads its reason, and does nothing when chosen", async () => {
    const user = userEvent.setup();
    const list = items();
    render(<Menu label="Export" items={list} />);
    await user.click(screen.getByRole("button", { name: "Export" }));
    const share = screen.getByRole("menuitem", { name: "Share" });
    expect(share.getAttribute("aria-disabled")).toBe("true");
    expect(share.getAttribute("aria-describedby")).toBeTruthy();
    expect(document.getElementById(share.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "Reports can be shared once saved.",
    );
    await user.click(share);
    expect(list[3]?.onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeTruthy();
  });

  it("closes on a click outside", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Menu label="Export" items={items()} />
        <p>Elsewhere</p>
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(screen.getByText("Elsewhere"));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("names an icon-only trigger by its label", () => {
    render(<Menu label="Actions for Acme Ltd" trigger="icon" items={items()} />);
    expect(screen.getByRole("button", { name: "Actions for Acme Ltd" }).getAttribute("aria-haspopup")).toBe("menu");
  });
});
