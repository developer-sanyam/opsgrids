import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SavedViewMenu } from "./SavedViewMenu";
import type { SavedViewItem, SavedViewMenuProps } from "./SavedViewMenu";

const VIEWS: SavedViewItem[] = [
  { id: "sys-mine", name: "My open deals", visibility: "private", mine: true, system: true },
  { id: "v1", name: "Hot in Pune", visibility: "private", mine: true },
  { id: "v2", name: "Q4 renewals", visibility: "org", mine: false },
];

function renderMenu(props: Partial<SavedViewMenuProps> = {}) {
  const handlers = {
    onSelect: vi.fn(),
    onSave: vi.fn(),
    onUpdate: vi.fn(),
    onDelete: vi.fn(),
  };
  render(<SavedViewMenu views={VIEWS} activeId={null} dirty={false} canShare={false} {...handlers} {...props} />);
  return handlers;
}

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Views/ }));
  return screen.getByRole("menu");
}

describe("SavedViewMenu", () => {
  it("lists All, the presets, the person's views and shared views, and selects one", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderMenu();
    const menu = await open(user);
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "All",
      "My open deals",
      "Hot in Pune",
      "Q4 renewals (shared)",
      "Save as view…",
    ]);
    await user.click(within(menu).getByRole("menuitem", { name: "Hot in Pune" }));
    expect(onSelect).toHaveBeenCalledWith("v1");
  });

  it("dirty_dot_has_text", () => {
    renderMenu({ dirty: true, activeId: "v1" });
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
  });

  it("offers Update only for the person's own active view with changes", async () => {
    const user = userEvent.setup();
    const { onUpdate } = renderMenu({ dirty: true, activeId: "v1" });
    await user.click(within(await open(user)).getByRole("menuitem", { name: "Update view" }));
    expect(onUpdate).toHaveBeenCalledWith("v1");
  });

  it("offers neither Update nor Delete for a shared or preset view", async () => {
    const user = userEvent.setup();
    renderMenu({ dirty: true, activeId: "v2" });
    const menu = await open(user);
    expect(within(menu).queryByRole("menuitem", { name: /Update view|Delete view/ })).toBeNull();
  });

  it("share_option_only_when_canShare", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(within(await open(user)).getByRole("menuitem", { name: "Save as view…" }));
    expect(screen.queryByRole("radio", { name: "Everyone in the organization" })).toBeNull();
  });

  it("saves a named view, shared when the person can share", async () => {
    const user = userEvent.setup();
    const { onSave } = renderMenu({ canShare: true });
    await user.click(within(await open(user)).getByRole("menuitem", { name: "Save as view…" }));
    const dialog = screen.getByRole("dialog", { name: "Save view" });

    await user.click(within(dialog).getByRole("button", { name: "Save view" }));
    expect(within(dialog).getByText("Name the view in 1 to 80 characters.")).toBeTruthy();
    expect(onSave).not.toHaveBeenCalled();

    await user.type(within(dialog).getByRole("textbox", { name: /Name/ }), "  Big tickets  ");
    await user.click(within(dialog).getByRole("radio", { name: "Everyone in the organization" }));
    await user.click(within(dialog).getByRole("button", { name: "Save view" }));
    expect(onSave).toHaveBeenCalledWith("Big tickets", "org");
  });

  it("delete_confirms", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderMenu({ activeId: "v1" });
    await user.click(within(await open(user)).getByRole("menuitem", { name: "Delete view" }));
    expect(onDelete).not.toHaveBeenCalled();
    const confirm = screen.getByRole("dialog", { name: "Delete “Hot in Pune”?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete view" }));
    expect(onDelete).toHaveBeenCalledWith("v1");
  });
});
