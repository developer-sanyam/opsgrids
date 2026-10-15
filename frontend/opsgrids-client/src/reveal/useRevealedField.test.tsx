/**
 * The hook on its own. Owner decision 2026-10-15 (ADR-0023 addendum): a permitted caller reveals
 * with one tap, no code; the value stays visible (no timer) and sticks for the session, so a
 * second look at the same record makes no call; sign-out empties it.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { clearRevealedValues } from "./revealedValues";
import { useRevealedField } from "./useRevealedField";

function problem(status: number, detail: string): ApiError {
  return new ApiError(status, detail, { status, detail });
}

interface Deferred {
  promise: Promise<string | null>;
  resolve: (value: string | null) => void;
}

function deferred(): Deferred {
  const parts: Partial<Pick<Deferred, "resolve">> = {};
  const promise = new Promise<string | null>((resolve) => {
    parts.resolve = resolve;
  });
  return { promise, resolve: (value) => parts.resolve?.(value) };
}

beforeEach(() => {
  clearRevealedValues();
});

afterEach(() => {
  vi.useRealTimers();
  clearRevealedValues();
});

function setup(load: () => Promise<string | null>, resetKey = "lead-1") {
  return renderHook(
    (props: { resetKey: string }) =>
      useRevealedField({ resetKey: props.resetKey, subject: "lead", noun: "phone number", load }),
    { initialProps: { resetKey } },
  );
}

describe("useRevealedField", () => {
  it("is masked until asked, then holds the value the load returned, with no code in between", async () => {
    const load = vi.fn().mockResolvedValue("+91 98765 04821");
    const { result } = setup(load);
    expect(result.current.value).toBeNull();
    expect(load).not.toHaveBeenCalled();

    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBe("+91 98765 04821");
    expect(result.current.pending).toBe(false);
    expect(load).toHaveBeenCalledExactlyOnceWith();
  });

  it("is pending from the press until the answer, and starts one reveal however often it is pressed", async () => {
    const answer = deferred();
    const load = vi.fn().mockReturnValue(answer.promise);
    const { result } = setup(load);

    act(() => {
      result.current.reveal();
      result.current.reveal();
    });
    expect(result.current.pending).toBe(true);
    expect(load).toHaveBeenCalledTimes(1);

    await act(async () => {
      answer.resolve("+91 98765 04821");
    });
    expect(result.current.pending).toBe(false);
  });

  it("does not hide on a timer: the value is still there long after the old thirty seconds", async () => {
    vi.useFakeTimers();
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    act(() => {
      vi.advanceTimersByTime(60 * 60 * 1000);
    });

    expect(result.current.value).toBe("+91 98765 04821");
  });

  it("is sticky: coming back to the same record shows the value and makes no second call", async () => {
    const load = vi.fn().mockResolvedValue("+91 98765 04821");
    const first = setup(load);
    await act(async () => {
      first.result.current.reveal();
    });
    first.unmount();

    const again = setup(load);

    expect(again.result.current.value).toBe("+91 98765 04821");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps one record's value off another record", async () => {
    const { result, rerender } = setup(vi.fn().mockResolvedValue("+91 98765 04821"), "lead-1");
    await act(async () => {
      result.current.reveal();
    });

    rerender({ resetKey: "lead-2" });

    expect(result.current.value).toBeNull();
  });

  it("forgets everything when the session ends", async () => {
    const first = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      first.result.current.reveal();
    });
    first.unmount();

    clearRevealedValues();

    expect(setup(vi.fn()).result.current.value).toBeNull();
  });

  it("never writes the value to browser storage", async () => {
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    expect(JSON.stringify({ ...window.localStorage })).not.toContain("98765");
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain("98765");
  });

  it("says so, neutrally, when nothing is on file, and caches nothing", async () => {
    const { result } = setup(vi.fn().mockResolvedValue(null));
    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBeNull();
    expect(result.current.notice).toEqual({ tone: "neutral", text: "No phone number is on file." });
  });

  it("words a refusal under the field and shows no value", async () => {
    const { result } = setup(
      vi.fn().mockRejectedValue(problem(403, "Missing permission: crm.lead.lead.read_sensitive")),
    );
    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBeNull();
    expect(result.current.notice?.tone).toBe("error");
    expect(result.current.notice?.text).toContain("don't have permission");
  });

  it("ignores an answer that arrives after the record changed", async () => {
    const answer = deferred();
    const { result, rerender } = setup(vi.fn().mockReturnValue(answer.promise), "lead-1");
    act(() => {
      result.current.reveal();
    });

    rerender({ resetKey: "lead-2" });
    await act(async () => {
      answer.resolve("+91 98765 04821");
    });

    expect(result.current.value).toBeNull();
    rerender({ resetKey: "lead-1" });
    expect(result.current.value).toBeNull();
  });
});
