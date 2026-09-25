/**
 * A drawer is a modal dialog that slides. The slide is CSS; what is pinned here is the dialog:
 * focus in, focus trapped, Escape out, focus back — and that it nests like one.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { Drawer } from "./Drawer";

function Harness({ dismissOnBackdrop = false, onClose }: { dismissOnBackdrop?: boolean; onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Show filters</Button>
      <Drawer
        open={open}
        onClose={() => {
          onClose?.();
          setOpen(false);
        }}
        title="Filters"
        description="Narrow the accounts list."
        dismissOnBackdrop={dismissOnBackdrop}
        footer={<Button onClick={() => setConfirming(true)}>Reset</Button>}
      >
        <p>Plan and status</p>
      </Drawer>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => setConfirming(false)}
        title="Reset every filter?"
        body="The list goes back to all accounts."
        confirmLabel="Reset"
        tone="primary"
      />
    </>
  );
}

describe("Drawer", () => {
  it("is a named, described modal dialog that takes focus on its heading", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Filters" }));
  });

  it("closes on Escape and gives focus back to what opened it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Show filters" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("keeps Tab inside the panel", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    const dialog = screen.getByRole("dialog");
    for (let press = 0; press < 6; press += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
  });

  it("lets a dialog opened from inside it own Escape until that one closes", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByRole("dialog", { name: "Reset every filter?" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Reset every filter?" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeTruthy();
  });

  it("ignores the backdrop unless told a click outside dismisses it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { unmount } = render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    const backdrop = document.querySelector<HTMLElement>(".bg-ink\\/50");
    if (backdrop === null) throw new Error("no backdrop");
    await user.click(backdrop);
    expect(onClose).not.toHaveBeenCalled();
    unmount();

    render(<Harness dismissOnBackdrop onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    const dismissable = document.querySelector<HTMLElement>(".bg-ink\\/50");
    if (dismissable === null) throw new Error("no backdrop");
    await user.click(dismissable);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("names its close control after the drawer", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Show filters" }));
    await user.click(screen.getByRole("button", { name: "Close Filters" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
