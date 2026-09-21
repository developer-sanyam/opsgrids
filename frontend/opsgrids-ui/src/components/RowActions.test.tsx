/**
 * A table of fifty rows is fifty copies of this component, so every mistake in it is made fifty
 * times: a button a keyboard cannot reach, fifty controls all called "Edit", a Delete that is
 * greyed out with no way to find out why.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DataTable } from "./DataTable";
import { RowActions } from "./RowActions";

describe("RowActions", () => {
  it("names the row in every action, so fifty Edit buttons are fifty different controls", () => {
    render(
      <RowActions
        label="Acme Ltd"
        actions={[
          { label: "View", onSelect: vi.fn() },
          { label: "Edit", onSelect: vi.fn() },
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: "Edit Acme Ltd" })).toBeTruthy();
    // WCAG 2.5.3: the accessible name still starts with the visible label.
    expect(screen.getByRole("button", { name: "Edit Acme Ltd" }).textContent).toBe("Edit");
  });

  it("is operable from the keyboard", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RowActions label="Acme Ltd" actions={[{ label: "Edit", onSelect }]} />);

    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit Acme Ltd" }));
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("leaves a hidden action out entirely, rather than showing a control that will be refused", () => {
    render(
      <RowActions
        label="Acme Ltd"
        actions={[
          { label: "Edit", onSelect: vi.fn() },
          { label: "Delete", onSelect: vi.fn(), tone: "danger", hidden: true },
        ]}
      />,
    );
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("keeps a disabled action focusable and says why it cannot be used", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <RowActions
        label="Invoice 4021"
        actions={[
          {
            label: "Delete",
            onSelect,
            disabled: true,
            disabledReason: "Paid invoices can't be deleted. Raise a credit note instead.",
          },
        ]}
      />,
    );

    const action = screen.getByRole("button", { name: /Delete Invoice 4021/ });
    expect(action.getAttribute("aria-disabled")).toBe("true");
    expect(action.hasAttribute("disabled")).toBe(false);

    await user.tab();
    expect(document.activeElement).toBe(action);
    await user.keyboard("{Enter}");
    expect(onSelect).not.toHaveBeenCalled();

    const describedBy = action.getAttribute("aria-describedby");
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain(
      "Raise a credit note instead.",
    );
  });

  it("keeps three actions in the row and gives four an overflow menu", () => {
    const three = ["View", "Edit", "Delete"].map((label) => ({ label, onSelect: vi.fn() }));
    const { unmount } = render(<RowActions label="Acme Ltd" actions={three} />);
    expect(screen.queryByRole("button", { name: /More actions/ })).toBeNull();
    unmount();

    render(
      <RowActions
        label="Acme Ltd"
        actions={[...three, { label: "Archive", onSelect: vi.fn() }]}
      />,
    );
    expect(screen.getByRole("button", { name: "More actions for Acme Ltd" })).toBeTruthy();
    // Two stay in the row; the rest move behind the menu.
    expect(screen.getByRole("button", { name: "View Acme Ltd" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete Acme Ltd" })).toBeNull();
  });

  it("drives the overflow menu with the arrow keys and chooses with Enter", async () => {
    const user = userEvent.setup();
    const onArchive = vi.fn();
    render(
      <RowActions
        label="Acme Ltd"
        actions={[
          { label: "View", onSelect: vi.fn() },
          { label: "Edit", onSelect: vi.fn() },
          { label: "Delete", onSelect: vi.fn(), tone: "danger" },
          { label: "Archive", onSelect: onArchive },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "More actions for Acme Ltd" }));
    const items = screen.getAllByRole("menuitem");
    expect(document.activeElement).toBe(items[0]);

    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(items[1]);
    await user.keyboard("{Enter}");

    expect(onArchive).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes the menu on Escape and puts focus back on the trigger", async () => {
    const user = userEvent.setup();
    render(
      <RowActions
        label="Acme Ltd"
        actions={["View", "Edit", "Delete", "Archive"].map((label) => ({
          label,
          onSelect: vi.fn(),
        }))}
      />,
    );

    const trigger = screen.getByRole("button", { name: "More actions for Acme Ltd" });
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("does not activate the row it sits in", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    const onSelect = vi.fn();
    // The real arrangement: `DataTable` makes a row with `onRowClick` an operable control, and
    // the actions cell lives inside it.
    render(
      <DataTable
        columns={[
          {
            key: "actions",
            header: "",
            align: "right",
            render: (row: { id: string }) => (
              <RowActions label={row.id} actions={[{ label: "Delete", onSelect }]} />
            ),
          },
        ]}
        rows={[{ id: "Acme Ltd" }]}
        rowKey={(row) => row.id}
        onRowClick={onRowClick}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Delete Acme Ltd" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it("gives every control a 44px hit area without growing the row", () => {
    render(
      <RowActions
        label="Acme Ltd"
        inlineLimit={2}
        actions={[
          { label: "Edit", onSelect: vi.fn() },
          { label: "Delete", tone: "danger", onSelect: vi.fn() },
          { label: "Archive", onSelect: vi.fn() },
        ]}
      />,
    );

    // `before:h-11` + `before:min-w-11` is the 44x44 target; the pill and the 32px trigger keep
    // their drawn size. jsdom has no layout, so the contract is pinned on the classes that make it.
    const edit = screen.getByRole("button", { name: "Edit Acme Ltd" });
    expect(edit.className).toContain("before:h-11");
    expect(edit.className).toContain("before:min-w-11");
    expect(edit.className).toContain("relative");

    const trigger = screen.getByRole("button", { name: "More actions for Acme Ltd" });
    expect(trigger.className).toContain("before:h-11");
    expect(trigger.className).toContain("before:min-w-11");
    expect(trigger.className).toContain("size-8");
  });

  it("gives the overflow menu items a 44px row of their own", async () => {
    const user = userEvent.setup();
    render(
      <RowActions
        label="Acme Ltd"
        inlineLimit={1}
        actions={[
          { label: "Edit", onSelect: vi.fn() },
          { label: "Delete", tone: "danger", onSelect: vi.fn() },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "More actions for Acme Ltd" }));
    for (const item of screen.getAllByRole("menuitem")) {
      expect(item.className).toContain("min-h-11");
    }
  });

  it("renders nothing when every action is hidden", () => {
    const { container } = render(
      <RowActions
        label="Acme Ltd"
        actions={[{ label: "Delete", onSelect: vi.fn(), hidden: true }]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
