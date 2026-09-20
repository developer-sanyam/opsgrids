/**
 * `ErrorState` is the one component every failure in the platform passes through, so its heading
 * is not decoration: a 403, a 404 and an offline state are different facts, and a component that
 * calls all three "Something went wrong" teaches users to ignore it.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EmptyState, ErrorState } from "./States";

describe("ErrorState", () => {
  it("keeps its default heading, so existing call sites are unchanged", () => {
    render(<ErrorState message="The gateway is unreachable." />);
    expect(screen.getByRole("alert").textContent).toContain("Something went wrong");
  });

  it("carries a caller's heading for a state that is not a fault", () => {
    render(
      <ErrorState
        title="That request isn't available to you"
        message="It may have been removed, or it may belong to someone else's chain."
      />,
    );
    expect(screen.getByText("That request isn't available to you")).toBeDefined();
    expect(screen.queryByText("Something went wrong")).toBeNull();
  });

  it("renders the problem's reference so support never has to ask for it", () => {
    render(<ErrorState message="Try again in a moment." reference="7f3c1a2e" />);
    expect(screen.getByText("Reference: 7f3c1a2e")).toBeDefined();
  });

  it("announces itself, because it replaces a loading state with no click", () => {
    render(<ErrorState message="Something failed." />);
    expect(screen.getByRole("alert")).toBeDefined();
  });

  it("retries from the keyboard", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorState message="Offline." onRetry={onRetry} />);
    await user.tab();
    await user.keyboard("{Enter}");
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("takes an action other than retry, for a state retrying cannot fix", () => {
    render(
      <ErrorState
        title="You're offline"
        message="Reconnect and try again. Nothing you typed has been lost."
        action={<a href="/approvals">Back to approvals</a>}
      />,
    );
    expect(screen.getByRole("link", { name: "Back to approvals" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("EmptyState", () => {
  it("says what happened and what to do next", () => {
    render(
      <EmptyState
        title="Nothing is waiting on you"
        description="When someone asks you to approve something, it lands here."
      />,
    );
    expect(screen.getByText("Nothing is waiting on you")).toBeDefined();
  });
});
