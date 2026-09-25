/**
 * A dialog's keyboard contract is the dialog. Focus goes in, stays in, and comes back to the
 * control that opened it — and every decision in this platform is taken inside one, so a trap
 * that leaks is a decision made with the screen reader somewhere else on the page.
 */

import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { openDialogCount } from "../lib/dialogStack";
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

/**
 * The shape the bug was reported in: "add a team member" opened from inside an open create-deal
 * form. Both dialogs listen on the document; the outer one registered first, so before the stack
 * existed Escape closed the form under the dialog the user was actually looking at.
 */
function NestedHarness() {
  const [dealOpen, setDealOpen] = useState(false);
  const [memberOpen, setMemberOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setDealOpen(true)}>New deal</Button>
      <Dialog
        open={dealOpen}
        onClose={() => setDealOpen(false)}
        title="New deal"
        footer={
          <Button variant="ghost" onClick={() => setDealOpen(false)}>
            Cancel deal
          </Button>
        }
      >
        <input aria-label="Deal name" />
        <Button onClick={() => setMemberOpen(true)}>Add a team member</Button>
        <Dialog
          open={memberOpen}
          onClose={() => setMemberOpen(false)}
          title="Add a team member"
          footer={
            <Button variant="ghost" onClick={() => setMemberOpen(false)}>
              Cancel member
            </Button>
          }
        >
          <input aria-label="Email" />
        </Dialog>
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

  /**
   * The bug this guards: some browsers' `:focus-visible` heuristic doesn't distinguish this
   * scripted `.focus()` from a keyboard tab, so the heading — or a form dialog's field — drew the
   * same ring a keyboard user gets, for a click that never touched the keyboard. `console.css`
   * suppresses the ring for exactly the element carrying this marker; this test is the JS half of
   * that fix — that the marker lands on the right element and lifts the moment focus moves on, so
   * a keyboard user who tabs there afterwards is never affected by it.
   */
  it("marks the initial focus target so it draws no ring, and clears the mark once focus moves on", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const heading = document.activeElement as HTMLElement;
    expect(heading.getAttribute("data-initial-focus")).toBe("");

    // The heading isn't itself tab-reachable (`tabIndex={-1}`), so a click on another control —
    // rather than `user.tab()` — is what actually moves focus off it here.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(heading.hasAttribute("data-initial-focus")).toBe(false);
  });

  it("marks a form dialog's initial field the same way, clearing it once focus moves elsewhere", async () => {
    const user = userEvent.setup();
    render(<Harness initialFocusField />);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    const field = screen.getByLabelText("Comment");
    expect(field.getAttribute("data-initial-focus")).toBe("");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(field.hasAttribute("data-initial-focus")).toBe(false);
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

  it("gives Escape to the dialog on top, and only to it", async () => {
    const user = userEvent.setup();
    render(<NestedHarness />);
    const opener = screen.getByRole("button", { name: "New deal" });
    await user.click(opener);
    const addMember = screen.getByRole("button", { name: "Add a team member" });
    await user.click(addMember);
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    expect(openDialogCount()).toBe(2);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Add a team member" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "New deal" })).toBeDefined();
    // Focus comes back to the nested dialog's own opener, which is inside the dialog beneath.
    expect(document.activeElement).toBe(addMember);

    // The dialog beneath has the keyboard again, without an effect re-running.
    await user.keyboard("{Escape}");
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    expect(document.activeElement).toBe(opener);
    // Nothing left on the stack: a leaked layer would make the next dialog think it is buried.
    expect(openDialogCount()).toBe(0);
  });

  it("traps Tab inside the topmost dialog while one is open on top of another", async () => {
    const user = userEvent.setup();
    render(<NestedHarness />);
    await user.click(screen.getByRole("button", { name: "New deal" }));
    await user.click(screen.getByRole("button", { name: "Add a team member" }));
    const inner = screen.getByRole("dialog", { name: "Add a team member" });

    screen.getByRole("button", { name: "Cancel member" }).focus();
    await user.tab();
    expect(inner.contains(document.activeElement)).toBe(true);

    screen.getByLabelText("Email").focus();
    await user.tab();
    expect(inner.contains(document.activeElement)).toBe(true);
  });

  it("closes the nested dialog without disturbing the one beneath", async () => {
    const user = userEvent.setup();
    render(<NestedHarness />);
    await user.click(screen.getByRole("button", { name: "New deal" }));
    await user.type(screen.getByLabelText("Deal name"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Add a team member" }));
    await user.click(screen.getByRole("button", { name: "Cancel member" }));

    expect(screen.queryByRole("dialog", { name: "Add a team member" })).toBeNull();
    // The form under it kept what was typed into it — it never unmounted.
    expect((screen.getByLabelText("Deal name") as HTMLInputElement).value).toBe("Acme renewal");
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

  /**
   * The bug this guards: a page wrapper carrying `.rise` (`animation-fill-mode: both`) keeps a
   * `transform` on itself after the animation ends, which makes it the containing block for any
   * `position: fixed` descendant. A dialog rendered inside that wrapper — rather than portalled
   * past it — gets sized and positioned against the wrapper's box instead of the viewport, which
   * is how "New deal"'s footer and "New contact"'s header end up off-screen. Portalling to
   * `document.body` is what keeps a transformed ancestor, present or future, from being able to
   * do that.
   */
  it("renders outside any transformed ancestor, even one that carries the page's own container", () => {
    const onClose = vi.fn();
    const { container } = render(
      <div style={{ transform: "translateY(0px)" }}>
        <Dialog open onClose={onClose} title="New deal">
          <p>Body</p>
        </Dialog>
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: "New deal" });
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it("keeps nesting working once both dialogs render into document.body", async () => {
    const user = userEvent.setup();
    const { container } = render(<NestedHarness />);
    await user.click(screen.getByRole("button", { name: "New deal" }));
    await user.click(screen.getByRole("button", { name: "Add a team member" }));

    const outer = screen.getByRole("dialog", { name: "New deal" });
    const inner = screen.getByRole("dialog", { name: "Add a team member" });
    // Both portalled straight to `<body>`, as siblings — not one nested inside the other's DOM,
    // even though "Add a team member" is opened from a control inside "New deal".
    expect(container.contains(outer)).toBe(false);
    expect(container.contains(inner)).toBe(false);
    // The panel's grandparent is the portal's own root node (`fixed inset-0` wrapper), which
    // `createPortal` appends straight to `<body>`.
    expect(outer.parentElement?.parentElement).toBe(document.body);
    expect(inner.parentElement?.parentElement).toBe(document.body);
  });
});
