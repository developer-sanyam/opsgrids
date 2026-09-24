/**
 * The part of a form dialog that is easy to get wrong is everything that is not a click: Enter
 * from inside a field, the browser's own validation bubbles appearing instead of ours, and the
 * fields still holding the last attempt when the dialog opens again.
 */

import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";
import { FormDialog } from "./FormDialog";
import { TextField } from "./TextField";

/** The fields live in here, so closing the dialog unmounts them and the next open is blank. */
function WidgetFields({ inputRef }: { inputRef: React.RefObject<HTMLInputElement | null> }) {
  const [name, setName] = useState("");
  return (
    <TextField
      ref={inputRef}
      label="Name"
      required
      value={name}
      onChange={(event) => setName(event.target.value)}
    />
  );
}

function Harness({ onSubmit, pending = false }: { onSubmit: () => void; pending?: boolean }) {
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button onClick={() => setOpen(true)}>New widget</Button>
      <FormDialog
        open={open}
        onClose={() => setOpen(false)}
        onSubmit={onSubmit}
        title="New widget"
        submitLabel="Create widget"
        pending={pending}
        initialFocusRef={inputRef}
      >
        <WidgetFields inputRef={inputRef} />
      </FormDialog>
    </>
  );
}

describe("FormDialog", () => {
  it("submits on Enter from inside a field, without reaching for the mouse", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.type(screen.getByLabelText("Name"), "Bracket{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("puts focus on the first field it wants answered", async () => {
    const user = userEvent.setup();
    render(<Harness onSubmit={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Name"));
  });

  it("carries noValidate, so a required field is our message and not the browser's bubble", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.click(screen.getByRole("button", { name: "Create widget" }));
    // The field is `required` and empty: with browser validation on, this submit never happens.
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("resets by unmounting — the next open does not hold the last attempt", async () => {
    const user = userEvent.setup();
    render(<Harness onSubmit={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.type(screen.getByLabelText("Name"), "Bracket");
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Bracket");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "New widget" }));
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "");
  });

  it("does not submit twice, and says it is busy, while the write is in flight", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} pending />);

    await user.click(screen.getByRole("button", { name: "New widget" }));
    const submit = screen.getByRole("button", { name: "Create widget" });
    expect(submit.getAttribute("aria-busy")).toBe("true");
    await user.type(screen.getByLabelText("Name"), "{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps the dialog open on Escape while the write is in flight", async () => {
    const user = userEvent.setup();
    render(<Harness onSubmit={vi.fn()} pending />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("shows a form-level failure above the actions, not on a field", async () => {
    const user = userEvent.setup();
    function ErrorHarness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>New widget</Button>
          <FormDialog
            open={open}
            onClose={() => setOpen(false)}
            onSubmit={vi.fn()}
            title="New widget"
            submitLabel="Create widget"
            error="A widget with that name already exists."
            reference="7f3c1a2e"
          >
            <p>Fields</p>
          </FormDialog>
        </>
      );
    }
    render(<ErrorHarness />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    expect(screen.getByText("A widget with that name already exists.")).toBeTruthy();
    expect(screen.getByText("Reference: 7f3c1a2e")).toBeTruthy();
  });

  it("does not let a nested dialog's submit reach the outer form", async () => {
    // React bubbles synthetic events through the component tree rather than the DOM tree, so a
    // `FormDialog` opened from inside another `<form>` — the owner picker's "add a team member"
    // from inside a create-company form is the real case — would otherwise also fire the outer
    // form's own submit handler purely because it is a React-tree ancestor, even though the
    // dialog itself is portalled to `document.body` and never a DOM descendant of it.
    const user = userEvent.setup();
    const outerSubmit = vi.fn();
    const innerSubmit = vi.fn();

    function NestedHarness() {
      const [open, setOpen] = useState(false);
      const inputRef = useRef<HTMLInputElement>(null);
      return (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            outerSubmit();
          }}
        >
          <Button onClick={() => setOpen(true)}>New widget</Button>
          <FormDialog
            open={open}
            onClose={() => setOpen(false)}
            onSubmit={innerSubmit}
            title="New widget"
            submitLabel="Create widget"
            initialFocusRef={inputRef}
          >
            <WidgetFields inputRef={inputRef} />
          </FormDialog>
        </form>
      );
    }

    render(<NestedHarness />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.type(screen.getByLabelText("Name"), "Bracket");
    await user.click(screen.getByRole("button", { name: "Create widget" }));

    expect(innerSubmit).toHaveBeenCalledTimes(1);
    expect(outerSubmit).not.toHaveBeenCalled();
  });

  it("styles the submit as the destructive act when the form is one, and only then", () => {
    const { rerender } = render(
      <FormDialog
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        title="Suspend Acme Ltd"
        submitLabel="Suspend account"
      >
        <p>fields</p>
      </FormDialog>,
    );
    const plain = screen.getByRole("button", { name: "Suspend account" });
    expect(plain.className).toContain("bg-accent");
    expect(plain.className).not.toContain("bg-negative");

    rerender(
      <FormDialog
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        title="Suspend Acme Ltd"
        submitLabel="Suspend account"
        submitTone="danger"
      >
        <p>fields</p>
      </FormDialog>,
    );
    const destructive = screen.getByRole("button", { name: "Suspend account" });
    expect(destructive.className).toContain("bg-negative");
    expect(destructive.className).not.toContain("bg-accent");
    // Cancel is never the loud one: focus and emphasis stay off the destructive act.
    expect(screen.getByRole("button", { name: "Cancel" }).className).not.toContain("bg-negative");
  });
});
