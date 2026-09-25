/**
 * A toast exists because the thing that raised it has gone. So what is pinned here is survival —
 * the dialog closes, the message stays — and the two announcement channels, because a failed
 * write that is only said politely gets read after the user has moved on.
 */

import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useToast } from "../lib/toast";
import { Dialog } from "./Dialog";
import { Toaster } from "./Toaster";

function Raiser() {
  const [open, setOpen] = useState(false);
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <button
        type="button"
        onClick={() => {
          setOpen(false);
          toast.success("Widget created.", { description: "Acme Bracket is in the list." });
        }}
      >
        Save
      </button>
      <button type="button" onClick={() => toast.error("That name is already taken.")}>
        Fail
      </button>
      <button type="button" onClick={() => toast.success("Saved.", { duration: null })}>
        Save sticky
      </button>
      {/* A real 5s wait is not worth a second of suite time; the clock is the same clock. */}
      <button type="button" onClick={() => toast.success("Saved briefly.", { duration: 40 })}>
        Save brief
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="New widget">
        <p>Fields</p>
      </Dialog>
    </>
  );
}

function renderToaster() {
  return render(
    <Toaster>
      <Raiser />
    </Toaster>,
  );
}

/** The two alternating nodes of one channel, read as the sentence currently in it. */
function liveText(kind: "polite" | "assertive"): string {
  const nodes = Array.from(document.querySelectorAll(`[aria-live="${kind}"]`));
  return nodes.map((node) => node.textContent ?? "").join("");
}

describe("Toaster", () => {
  it("survives the dialog that raised it closing", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Widget created.")).toBeTruthy();
    expect(screen.getByText("Acme Bracket is in the list.")).toBeTruthy();
  });

  it("announces a success politely and an error assertively", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(liveText("polite")).toContain("Widget created.");
    expect(liveText("assertive")).toBe("");

    await user.click(screen.getByRole("button", { name: "Fail" }));
    expect(liveText("assertive")).toContain("That name is already taken.");
  });

  it("prefixes the tone in the announcement, so the colour is never the only carrier", async () => {
    const user = userEvent.setup();
    renderToaster();
    await user.click(screen.getByRole("button", { name: "Fail" }));
    expect(liveText("assertive")).toContain("Error:");
  });

  it("re-announces the same sentence twice in a row", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Fail" }));
    const first = liveText("assertive");
    await user.click(screen.getByRole("button", { name: "Fail" }));
    // The text moved to the other node of the pair; a region holding identical text would not
    // have been re-read.
    const nodes = Array.from(document.querySelectorAll('[aria-live="assertive"]'));
    expect(nodes.filter((node) => node.textContent !== "")).toHaveLength(1);
    expect(liveText("assertive")).toBe(first);
  });

  it("stacks rather than replacing — two writes produce two toasts", async () => {
    const user = userEvent.setup();
    renderToaster();
    await user.click(screen.getByRole("button", { name: "Save sticky" }));
    await user.click(screen.getByRole("button", { name: "Fail" }));

    expect(screen.getByText("Saved.")).toBeTruthy();
    expect(screen.getByText("That name is already taken.")).toBeTruthy();
  });

  it("dismisses itself when its time is up", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Save brief" }));
    expect(screen.getByText("Saved briefly.")).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryByText("Saved briefly.")).toBeNull();
    });
  });

  it("has a dismiss button that names what it is dismissing", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Fail" }));
    await user.click(screen.getByRole("button", { name: "Dismiss: That name is already taken." }));
    expect(screen.queryByText("That name is already taken.")).toBeNull();
  });

  it("holds while the pointer is over it, so it cannot expire mid-read", async () => {
    const user = userEvent.setup();
    renderToaster();

    await user.click(screen.getByRole("button", { name: "Save brief" }));
    await user.hover(screen.getByText("Saved briefly."));
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(screen.getByText("Saved briefly.")).toBeTruthy();

    // And resumes when the pointer leaves, with what was left of the clock rather than a fresh one.
    await user.unhover(screen.getByText("Saved briefly."));
    await waitFor(() => {
      expect(screen.queryByText("Saved briefly.")).toBeNull();
    });
  });

  it("a duration of null stays until it is dismissed", async () => {
    const user = userEvent.setup();
    renderToaster();
    await user.click(screen.getByRole("button", { name: "Save sticky" }));
    await user.click(screen.getByRole("button", { name: "Save brief" }));

    // The brief one going is the proof that time has passed for both.
    await waitFor(() => {
      expect(screen.queryByText("Saved briefly.")).toBeNull();
    });
    expect(screen.getByText("Saved.")).toBeTruthy();
  });

  it("has a warning and a system tone, each announced politely with its own word", async () => {
    const user = userEvent.setup();
    function ToneRaiser() {
      const toast = useToast();
      return (
        <>
          <button type="button" onClick={() => toast.warning("Invoice sent.", { description: "Reminders are off: no email on file." })}>
            Warn
          </button>
          <button type="button" onClick={() => toast.system("Scheduled maintenance tonight at 23:00 IST.")}>
            Notice
          </button>
        </>
      );
    }
    render(
      <Toaster>
        <ToneRaiser />
      </Toaster>,
    );
    await user.click(screen.getByRole("button", { name: "Warn" }));
    expect(liveText("polite")).toBe("Warning: Invoice sent. Reminders are off: no email on file.");
    await user.click(screen.getByRole("button", { name: "Notice" }));
    expect(liveText("polite")).toBe("System: Scheduled maintenance tonight at 23:00 IST.");
    expect(liveText("assertive")).toBe("");
    expect(screen.getByText("Invoice sent.")).toBeTruthy();
    expect(screen.getByText("Scheduled maintenance tonight at 23:00 IST.")).toBeTruthy();
  });

  it("is a no-op outside a Toaster rather than a crash", async () => {
    const user = userEvent.setup();
    render(<Raiser />);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.queryByText("Widget created.")).toBeNull();
  });
});
