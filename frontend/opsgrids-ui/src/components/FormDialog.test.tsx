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
});

/** The unsaved-changes guard: opt-in through `dirty`, asked before anything typed is thrown away. */
describe("FormDialog dirty", () => {
  /** Dirty once the name differs from what the form opened with — typed and deleted is clean. */
  function DirtyFields({ onDirty }: { onDirty: (dirty: boolean) => void }) {
    const [name, setName] = useState("");
    return (
      <TextField
        label="Name"
        value={name}
        onChange={(event) => {
          setName(event.target.value);
          onDirty(event.target.value !== "");
        }}
      />
    );
  }

  function DirtyHarness({ onClose = vi.fn() }: { onClose?: () => void }) {
    const [open, setOpen] = useState(false);
    const [dirty, setDirty] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>New widget</Button>
        <FormDialog
          open={open}
          onClose={() => {
            onClose();
            setOpen(false);
            setDirty(false);
          }}
          onSubmit={vi.fn()}
          title="New widget"
          submitLabel="Create widget"
          dirty={dirty}
        >
          <DirtyFields onDirty={setDirty} />
        </FormDialog>
      </>
    );
  }

  it("closes at once when nothing has been entered", async () => {
    const user = userEvent.setup();
    render(<DirtyHarness />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("asks before Escape discards what was typed, and Keep editing goes back to the form", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DirtyHarness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.type(screen.getByLabelText("Name"), "Bracket");
    await user.keyboard("{Escape}");

    expect(screen.getByRole("dialog", { name: "Discard unsaved changes?" })).toBeTruthy();
    // Focus is on the safe choice, and Escape here means that choice too.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Keep editing" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Discard unsaved changes?" })).toBeNull();
    expect(screen.getByLabelText("Name")).toHaveProperty("value", "Bracket");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes both, and gives focus back to the opener, on Discard changes", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DirtyHarness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "New widget" });
    await user.click(opener);
    await user.type(screen.getByLabelText("Name"), "Bracket");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("asks the browser to confirm a reload while the form is dirty and open", async () => {
    const user = userEvent.setup();
    render(<DirtyHarness />);
    await user.click(screen.getByRole("button", { name: "New widget" }));
    await user.type(screen.getByLabelText("Name"), "B");
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});

describe("FormDialog destructive", () => {
  it("draws the submit in the destructive colour only when asked", () => {
    const { rerender } = render(
      <FormDialog open onClose={vi.fn()} onSubmit={vi.fn()} title="Suspend" submitLabel="Suspend account">
        <p>Are you sure</p>
      </FormDialog>,
    );
    expect(screen.getByRole("button", { name: "Suspend account" }).className).not.toContain("bg-negative");
    rerender(
      <FormDialog open destructive onClose={vi.fn()} onSubmit={vi.fn()} title="Suspend" submitLabel="Suspend account">
        <p>Are you sure</p>
      </FormDialog>,
    );
    expect(screen.getByRole("button", { name: "Suspend account" }).className).toContain("bg-negative");
  });
});
