/**
 * A destructive confirmation is the last thing between a user and an irreversible write, so the
 * cases worth pinning are the ones where it fires when it should not: a stray Enter on open, a
 * double click, an Escape mid-flight that hides whether the delete happened.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";

function Harness({
  onConfirm,
  pending = false,
  error,
}: {
  onConfirm: () => void;
  pending?: boolean;
  error?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Delete widget</Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={onConfirm}
        title="Delete this widget?"
        body="Acme Bracket and its 3 revisions are removed. This can't be undone."
        confirmLabel="Delete"
        pending={pending}
        {...(error !== undefined ? { error } : {})}
      />
    </>
  );
}

describe("ConfirmDialog", () => {
  it("opens with focus on Cancel, never on the destructive button", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    await user.click(screen.getByRole("button", { name: "Delete widget" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));

    // The Enter that would otherwise land on Delete cancels instead.
    await user.keyboard("{Enter}");
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("confirms once however many times the button is clicked", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    await user.click(screen.getByRole("button", { name: "Delete widget" }));
    const confirm = screen.getByRole("button", { name: "Delete" });
    await user.click(confirm);
    await user.click(confirm);
    await user.click(confirm);

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("states the consequence in the body, tied to the dialog for a screen reader", async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Delete widget" }));

    const dialog = screen.getByRole("dialog");
    const describedBy = dialog.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain(
      "This can't be undone.",
    );
  });

  it("disables both controls while the write is in flight", async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={vi.fn()} pending />);
    await user.click(screen.getByRole("button", { name: "Delete widget" }));

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveProperty("disabled", true);
    const confirm = screen.getByRole("button", { name: "Delete" });
    expect(confirm).toHaveProperty("disabled", true);
    expect(confirm.getAttribute("aria-busy")).toBe("true");
  });

  it("refuses to close mid-flight, so the user is never left guessing whether it happened", async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={vi.fn()} pending />);
    await user.click(screen.getByRole("button", { name: "Delete widget" }));

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("returns focus to the control that opened it", async () => {
    const user = userEvent.setup();
    render(<Harness onConfirm={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Delete widget" });

    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(trigger);
  });

  it("shows the server's sentence and offers another attempt when the confirm failed", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} error="That widget is in use by 2 orders." />);
    await user.click(screen.getByRole("button", { name: "Delete widget" }));

    expect(screen.getByText("That widget is in use by 2 orders.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
