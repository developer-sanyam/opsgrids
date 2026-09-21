/**
 * The three failures this hook exists to make impossible: a duplicate row from a double click, a
 * validation message stranded at the top of a form, and a retried write.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "./http";
import { useMutation } from "./useMutation";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function Harness({
  fn,
  onSuccess,
}: {
  fn: (input: string) => Promise<string>;
  onSuccess?: (result: string) => void;
}) {
  const [saved, setSaved] = useState<string | null>(null);
  const { mutate, pending, error, fieldErrors, reset } = useMutation(fn, {
    onSuccess: (result) => {
      setSaved(result);
      onSuccess?.(result);
    },
  });
  return (
    <>
      <button type="button" onClick={() => void mutate("widget")} disabled={false}>
        Save
      </button>
      <button type="button" onClick={reset}>
        Reset
      </button>
      <p>pending: {pending ? "yes" : "no"}</p>
      {saved ? <p>saved: {saved}</p> : null}
      {error ? <p>error: {error.message}</p> : null}
      {fieldErrors.name ? <p>name: {fieldErrors.name}</p> : null}
    </>
  );
}

describe("useMutation", () => {
  it("ignores a second call while one is in flight", async () => {
    const user = userEvent.setup();
    const gate = deferred<string>();
    const fn = vi.fn(() => gate.promise);
    render(<Harness fn={fn} />);

    const save = screen.getByRole("button", { name: "Save" });
    await user.click(save);
    await user.click(save);
    await user.click(save);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(screen.getByText("pending: yes")).toBeTruthy();

    gate.resolve("Widget A");
    await screen.findByText("saved: Widget A");
    expect(screen.getByText("pending: no")).toBeTruthy();
  });

  it("allows the next write once the first has landed", async () => {
    const user = userEvent.setup();
    const fn = vi.fn((input: string) => Promise.resolve(input));
    render(<Harness fn={fn} />);

    const save = screen.getByRole("button", { name: "Save" });
    await user.click(save);
    await screen.findByText("saved: widget");
    await user.click(save);

    await waitFor(() => {
      expect(fn).toHaveBeenCalledTimes(2);
    });
  });

  it("maps a 422's errors[] onto the field that caused it", async () => {
    const user = userEvent.setup();
    const fn = vi.fn(() =>
      Promise.reject(
        new ApiError(422, "Request payload failed validation", {
          detail: "Request payload failed validation",
          errors: [
            { field: "name", code: "string_too_short", message: "Give it a name of at least 2 characters." },
          ],
        }),
      ),
    );
    render(<Harness fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("name: Give it a name of at least 2 characters.");
    expect(screen.getByText("pending: no")).toBeTruthy();
  });

  it("leaves fieldErrors empty for a failure that names no field", async () => {
    const user = userEvent.setup();
    const fn = vi.fn(() => Promise.reject(new ApiError(403, "Not permitted", { detail: "Not permitted" })));
    render(<Harness fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("error: Not permitted");
    expect(screen.queryByText(/^name: /)).toBeNull();
  });

  it("never retries — one call per click, whatever the status", async () => {
    const user = userEvent.setup();
    const fn = vi.fn(() => Promise.reject(new ApiError(503, "Service unavailable", null)));
    render(<Harness fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("error: Service unavailable");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("reset clears the error and the field messages", async () => {
    const user = userEvent.setup();
    const fn = vi.fn(() =>
      Promise.reject(
        new ApiError(422, "Validation failed", {
          errors: [{ field: "name", code: "missing", message: "Field required" }],
        }),
      ),
    );
    render(<Harness fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("name: Field required");
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.queryByText("name: Field required")).toBeNull();
    expect(screen.queryByText(/^error: /)).toBeNull();
  });

  it("wraps a non-Error rejection so callers always get an Error", async () => {
    const user = userEvent.setup();
    const fn = vi.fn(() => Promise.reject("nope"));
    render(<Harness fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("error: nope");
  });

  it("does not run onSuccess for a call it skipped", async () => {
    const user = userEvent.setup();
    const gate = deferred<string>();
    const onSuccess = vi.fn();
    render(<Harness fn={() => gate.promise} onSuccess={onSuccess} />);

    const save = screen.getByRole("button", { name: "Save" });
    await user.click(save);
    await user.click(save);
    gate.resolve("Widget A");
    await screen.findByText("saved: Widget A");
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
});
