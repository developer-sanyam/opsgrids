/**
 * A dialog's keyboard contract is the dialog. Focus goes in, stays in, and comes back to the
 * control that opened it — and every decision in this platform is taken inside one, so a trap
 * that leaks is a decision made with the screen reader somewhere else on the page.
 */

import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";
import { Dialog } from "./Dialog";

function Harness({ initialFocusField = false }: { initialFocusField?: boolean }) {
  const [open, setOpen] = useState(false);
  const commentRef = useRef<HTMLTextAreaElement>(null);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Reject</Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Reject this correction?"
        description="Ananya Iyer's attendance for 12 Sep stays Absent. This can't be undone."
        {...(initialFocusField ? { initialFocusRef: commentRef } : {})}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger">Reject</Button>
          </>
        }
      >
        <textarea ref={commentRef} aria-label="Comment" />
      </Dialog>
    </>
  );
}

/**
 * The reject dialog's comment and the regularisation dialog's reason are both controlled: the
 * parent holds the value in state and passes `onClose` as a fresh inline arrow on every render, so
 * every keystroke re-renders the parent. A form dialog must survive that. This mirrors the real
 * reject dialog's shape — initial focus lands on the heading, and the caller clicks into the field
 * before typing, the way an operator actually would.
 */
function ControlledHarness() {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  return (
    <>
      <Button onClick={() => setOpen(true)}>Reject</Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Reject this correction?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger">Reject</Button>
          </>
        }
      >
        <textarea
          aria-label="Comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
        />
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("is a modal named by its heading", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this correction?" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("lands focus on the heading, so the consequence is read before the actions", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(document.activeElement?.textContent).toBe("Reject this correction?");
  });

  it("lands focus on the field a form dialog wants answered", async () => {
    const user = userEvent.setup();
    render(<Harness initialFocusField />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Comment"));
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("returns focus to the control that opened it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Reject" });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(opener);
  });

  it("traps Tab inside itself", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    // Walk to the last control in the dialog, then once more — it must come back to the first.
    const controls = screen.getAllByRole("button");
    const last = controls[controls.length - 1];
    last?.focus();
    await user.tab();
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("keeps focus in a controlled field across keystrokes, so typing never loses the cursor", async () => {
    const user = userEvent.setup();
    render(<ControlledHarness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const field = screen.getByLabelText("Comment") as HTMLTextAreaElement;
    await user.click(field);
    expect(document.activeElement).toBe(field);
    await user.type(field, "Missing manager sign-off");
    expect(field.value).toBe("Missing manager sign-off");
    expect(document.activeElement).toBe(field);
  });

  it("renders nothing at all when closed", () => {
    const onClose = vi.fn();
    render(
      <Dialog open={false} onClose={onClose} title="Approve this correction?">
        <p>Body</p>
      </Dialog>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
