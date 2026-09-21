/**
 * The loading state is the sixth state, and the one that is usually bolted on: a button that
 * keeps accepting clicks while its write is in flight, or that changes width when its label
 * becomes "Saving…" and moves Cancel out from under the pointer.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "./Button";

describe("Button", () => {
  it("cannot be clicked while it is loading", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Create widget
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Create widget" });
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
    expect(button).toHaveProperty("disabled", true);
  });

  it("says it is busy rather than leaving it to a spinner nobody can see", () => {
    render(<Button loading>Create widget</Button>);
    expect(screen.getByRole("button").getAttribute("aria-busy")).toBe("true");
  });

  it("carries no aria-busy at rest", () => {
    render(<Button>Create widget</Button>);
    expect(screen.getByRole("button").hasAttribute("aria-busy")).toBe(false);
  });

  it("keeps the label in the layout so the button does not resize mid-submit", () => {
    render(<Button loading>Create widget</Button>);
    // Present and measured, just not painted — `invisible`, never `display: none`.
    const label = screen.getByText("Create widget");
    expect(label.className).toContain("invisible");
  });

  it("stays a button the accessible name still reaches", () => {
    render(<Button loading>Create widget</Button>);
    expect(screen.getByRole("button", { name: "Create widget" })).toBeTruthy();
  });

  it("forwards a ref, so a dialog can put focus on it", () => {
    let node: HTMLButtonElement | null = null;
    render(
      <Button
        ref={(element) => {
          node = element;
        }}
      >
        Cancel
      </Button>,
    );
    expect(node).toBe(screen.getByRole("button", { name: "Cancel" }));
  });

  it("is still disabled by its own prop when it is not loading", () => {
    render(<Button disabled>Create widget</Button>);
    expect(screen.getByRole("button")).toHaveProperty("disabled", true);
  });
});
