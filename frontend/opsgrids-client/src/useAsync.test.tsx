/**
 * `reload` is the join between a write landing and the list telling the truth again. It replaced
 * a `reloadKey` nonce that three screens were threading through `deps` by hand.
 */

import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useAsync } from "./useAsync";

function Harness({ loader, id = "a" }: { loader: () => Promise<string>; id?: string }) {
  const { status, data, error, reload, refresh, refreshing } = useAsync(loader, [id]);
  return (
    <>
      <button type="button" onClick={reload}>
        Reload
      </button>
      <button type="button" onClick={refresh}>
        Refresh
      </button>
      <p>status: {status}</p>
      {refreshing ? <p>refreshing</p> : null}
      {data ? <p>data: {data}</p> : null}
      {error ? <p>error: {error.message}</p> : null}
    </>
  );
}

/** Pending loads after the first, resolved by hand. */
function controlledLoader(first: string) {
  const pending: ((value: string) => void)[] = [];
  const loader = vi.fn(() => {
    if (loader.mock.calls.length === 1) return Promise.resolve(first);
    return new Promise<string>((res) => {
      pending.push(res);
    });
  });
  return { loader, resolveNext: (value: string) => pending.shift()?.(value) };
}

describe("useAsync", () => {
  it("runs the loader again on reload and shows the new answer", async () => {
    const user = userEvent.setup();
    let call = 0;
    const loader = vi.fn(() => Promise.resolve(`page ${++call}`));
    render(<Harness loader={loader} />);

    await screen.findByText("data: page 1");
    await user.click(screen.getByRole("button", { name: "Reload" }));
    await screen.findByText("data: page 2");
  });

  it("re-enters loading on reload rather than holding rows that may be gone", async () => {
    const user = userEvent.setup();
    let resolve!: (value: string) => void;
    const loader = vi.fn(() => {
      if (loader.mock.calls.length === 1) return Promise.resolve("first");
      return new Promise<string>((res) => {
        resolve = res;
      });
    });
    render(<Harness loader={loader} />);

    await screen.findByText("data: first");
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(screen.getByText("status: loading")).toBeTruthy();
    expect(screen.queryByText("data: first")).toBeNull();

    resolve("second");
    await screen.findByText("data: second");
  });

  it("keeps what is shown on screen through a refresh, and says it is refreshing", async () => {
    const user = userEvent.setup();
    const { loader, resolveNext } = controlledLoader("first");
    render(<Harness loader={loader} />);

    await screen.findByText("data: first");
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByText("status: success")).toBeTruthy();
    expect(screen.getByText("data: first")).toBeTruthy();
    expect(screen.getByText("refreshing")).toBeTruthy();

    act(() => resolveNext("second"));
    await screen.findByText("data: second");
    expect(screen.queryByText("refreshing")).toBeNull();
  });

  it("does not keep another key's data when the deps change", async () => {
    const { loader } = controlledLoader("first");
    const { rerender } = render(<Harness loader={loader} id="a" />);
    await screen.findByText("data: first");
    rerender(<Harness loader={loader} id="b" />);
    expect(screen.getByText("status: loading")).toBeTruthy();
    expect(screen.queryByText("data: first")).toBeNull();
  });

  it("still reports a failed load as an error", async () => {
    const loader = vi.fn(() => Promise.reject(new Error("Could not read widgets")));
    render(<Harness loader={loader} />);
    await screen.findByText("error: Could not read widgets");
    expect(screen.getByText("status: error")).toBeTruthy();
  });
});
