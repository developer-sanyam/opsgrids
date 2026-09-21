/**
 * `reload` is the join between a write landing and the list telling the truth again. It replaced
 * a `reloadKey` nonce that three screens were threading through `deps` by hand.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useAsync } from "./useAsync";

function Harness({ loader }: { loader: () => Promise<string> }) {
  const { status, data, error, reload } = useAsync(loader, []);
  return (
    <>
      <button type="button" onClick={reload}>
        Reload
      </button>
      <p>status: {status}</p>
      {data ? <p>data: {data}</p> : null}
      {error ? <p>error: {error.message}</p> : null}
    </>
  );
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

  it("still reports a failed load as an error", async () => {
    const loader = vi.fn(() => Promise.reject(new Error("Could not read widgets")));
    render(<Harness loader={loader} />);
    await screen.findByText("error: Could not read widgets");
    expect(screen.getByText("status: error")).toBeTruthy();
  });
});
