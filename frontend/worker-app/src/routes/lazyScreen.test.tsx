/**
 * A lazy screen whose chunk will not download — the worker opened Put away for the first time with
 * no signal — must cost only that screen: the shell around it stays, the failure is said plainly,
 * and Try again asks for the module again (a `lazy` that has failed would rethrow its old rejection
 * for ever, so this is not what a bare `Suspense` gives).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { lazyScreen } from "./lazyScreen";

const CHUNK_FAILED = () => new TypeError("Failed to fetch dynamically imported module: /assets/Putaway-abc123.js");

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <nav aria-label="Shell">the shell</nav>
      {children}
    </div>
  );
}

describe("lazyScreen", () => {
  it("shows the screen once it has loaded, after the same fallback every lazy screen has", async () => {
    const Screen = lazyScreen("Put away", () => Promise.resolve({ default: () => <p>The put-away screen</p> }));

    render(
      <Shell>
        <Screen />
      </Shell>,
    );

    expect(await screen.findByText("The put-away screen")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("when the download fails it says so in the screen's own place, with the shell still up and a way to retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Screen = lazyScreen("Put away", () => Promise.reject(CHUNK_FAILED()));

    render(
      <Shell>
        <Screen />
      </Shell>,
    );

    expect(await screen.findByText("Put away couldn't be opened")).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/without a signal it may not load/)).toBeTruthy();
    expect(screen.getByText(/already saved on this phone keep syncing/)).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Shell" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    // The module's own error text is machinery, and carries a script URL: not shown.
    expect(screen.queryByText(/dynamically imported/)).toBeNull();
  });

  it("Try again asks for the module again, and the screen opens once the signal is back", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const load = vi
      .fn<() => Promise<{ default: () => React.ReactElement }>>()
      .mockRejectedValueOnce(CHUNK_FAILED())
      .mockResolvedValueOnce({ default: () => <p>The put-away screen</p> });
    const Screen = lazyScreen("Put away", load);
    const user = userEvent.setup();
    render(<Screen />);
    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("The put-away screen")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a retry that fails again says so again, and can be tried a third time", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const load = vi
      .fn<() => Promise<{ default: () => React.ReactElement }>>()
      .mockRejectedValueOnce(CHUNK_FAILED())
      .mockRejectedValueOnce(CHUNK_FAILED())
      .mockResolvedValueOnce({ default: () => <p>The put-away screen</p> });
    const Screen = lazyScreen("Put away", load);
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(await screen.findByRole("button", { name: "Try again" }));
    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("The put-away screen")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("asks for the module once however many times the screen is opened: a loaded screen opens again at once", async () => {
    const load = vi.fn(() => Promise.resolve({ default: () => <p>The put-away screen</p> }));
    const Screen = lazyScreen("Put away", load);

    const first = render(<Screen />);
    await screen.findByText("The put-away screen");
    first.unmount();
    render(<Screen />);

    expect(screen.getByText("The put-away screen")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
