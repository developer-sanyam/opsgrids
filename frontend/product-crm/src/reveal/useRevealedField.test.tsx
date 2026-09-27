/**
 * The hook on its own, with a step-up that answers at once, so what is pinned here is the hook's
 * own promises: the value lives only as long as its key, its timer and its component; a closed
 * dialog is silent; and an answer nobody is waiting for any more is never shown, now or later.
 * The screens' tests run the real step-up under it.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, StepUpCancelledError } from "@opsgrids/client";
import type { RevealHeaders, UseStepUpResult } from "@opsgrids/client";

import { REVEAL_VISIBLE_SECONDS, useRevealedField } from "./useRevealedField";
import type { RevealedFieldOptions } from "./useRevealedField";

const HEADERS: RevealHeaders = { Authorization: "Bearer step-up" };

function problem(status: number, detail: string): ApiError {
  return new ApiError(status, detail, { status, detail });
}

interface Deferred {
  promise: Promise<string | null>;
  resolve: (value: string | null) => void;
  reject: (error: unknown) => void;
}

function deferred(): Deferred {
  const parts: Partial<Pick<Deferred, "resolve" | "reject">> = {};
  const promise = new Promise<string | null>((resolve, reject) => {
    parts.resolve = resolve;
    parts.reject = reject;
  });
  return {
    promise,
    resolve: (value) => parts.resolve?.(value),
    reject: (error) => parts.reject?.(error),
  };
}

let clear: ReturnType<typeof vi.fn>;
let run: ReturnType<typeof vi.fn>;

function fakeStepUp(): UseStepUpResult {
  return {
    run: run as unknown as UseStepUpResult["run"],
    state: "idle",
    clear: clear as unknown as () => void,
    dialogProps: {
      open: false,
      busy: false,
      error: undefined,
      mfaState: "ready",
      onSubmit: () => undefined,
      onCancel: () => undefined,
      securityHref: "/account/security",
    },
  };
}

beforeEach(() => {
  clear = vi.fn();
  run = vi.fn((reveal: (headers: RevealHeaders) => Promise<unknown>) => reveal(HEADERS));
});

afterEach(() => {
  vi.useRealTimers();
});

/** Every render's key and value, in order — what a screen would have painted. */
let painted: { key: string; value: string | null }[];

function setup(load: RevealedFieldOptions["load"], resetKey = "lead-1") {
  const stepUp = fakeStepUp();
  painted = [];
  return renderHook(
    (props: { resetKey: string }) => {
      const field = useRevealedField({ resetKey: props.resetKey, stepUp, subject: "lead", noun: "phone number", load });
      painted.push({ key: props.resetKey, value: field.value });
      return field;
    },
    { initialProps: { resetKey } },
  );
}

describe("useRevealedField", () => {
  it("is masked until asked, then holds the value the load returned, given the step-up headers", async () => {
    const load = vi.fn().mockResolvedValue("+91 98765 04821");
    const { result } = setup(load);
    expect(result.current.value).toBeNull();

    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBe("+91 98765 04821");
    expect(result.current.pending).toBe(false);
    expect(load).toHaveBeenCalledExactlyOnceWith(HEADERS);
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

  it("hides on Hide, and says it was hidden", async () => {
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    act(() => {
      result.current.hide();
    });

    expect(result.current.value).toBeNull();
    expect(result.current.justHidden).toBe(true);
  });

  it("hides by itself once the visible time is up, and not a moment before", async () => {
    vi.useFakeTimers();
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    act(() => {
      vi.advanceTimersByTime(REVEAL_VISIBLE_SECONDS * 1000 - 1);
    });
    expect(result.current.value).toBe("+91 98765 04821");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.value).toBeNull();
    expect(result.current.justHidden).toBe(true);
  });

  it("starts the clock again on a second reveal, so an earlier timer cannot cut it short", async () => {
    vi.useFakeTimers();
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });
    act(() => {
      vi.advanceTimersByTime(20_000);
      result.current.hide();
    });
    await act(async () => {
      result.current.reveal();
    });

    act(() => {
      vi.advanceTimersByTime(20_000);
    });

    expect(result.current.value).toBe("+91 98765 04821");
  });

  it("drops the value the moment the key changes, with no render in between that shows it", async () => {
    const { result, rerender } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    rerender({ resetKey: "lead-2" });

    // Not one render for the other lead carried the first lead's number.
    const forSecond = painted.filter((render) => render.key === "lead-2");
    expect(forSecond.length).toBeGreaterThan(0);
    expect(forSecond.every((render) => render.value === null)).toBe(true);
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it("never shows an answer that arrives after the key changed, not even when the key comes back", async () => {
    const answer = deferred();
    const { result, rerender } = setup(vi.fn().mockReturnValue(answer.promise));
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
    expect(result.current.pending).toBe(false);
  });

  it("can reveal again for the new key while the old key's answer is still on its way", async () => {
    const stale = deferred();
    const load = vi.fn().mockReturnValueOnce(stale.promise).mockResolvedValueOnce("+91 90000 01177");
    const { result, rerender } = setup(load);
    act(() => {
      result.current.reveal();
    });

    rerender({ resetKey: "lead-2" });
    await act(async () => {
      result.current.reveal();
    });
    await act(async () => {
      stale.resolve("+91 98765 04821");
    });

    expect(result.current.value).toBe("+91 90000 01177");
  });

  it("drops the value, the timer and the code's access when its component goes away", async () => {
    vi.useFakeTimers();
    const { result, unmount } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));
    await act(async () => {
      result.current.reveal();
    });

    unmount();

    expect(clear).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("leaves the code's access alone when nothing was ever asked for", () => {
    const { unmount } = setup(vi.fn());

    unmount();

    expect(clear).not.toHaveBeenCalled();
  });

  it("is silent when the dialog is closed: no value, no notice, ready to be asked again", async () => {
    run.mockRejectedValueOnce(new StepUpCancelledError());
    const { result } = setup(vi.fn());

    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBeNull();
    expect(result.current.notice).toBeNull();
    expect(result.current.pending).toBe(false);
  });

  it("says there is nothing on file when the contact has no sealed number, as a neutral notice", async () => {
    const { result } = setup(vi.fn().mockResolvedValue(null));

    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.value).toBeNull();
    expect(result.current.notice).toEqual({ tone: "neutral", text: "No phone number is on file." });
  });

  it("turns a refusal into a sentence, and keeps the code's access for the next field", async () => {
    const { result } = setup(vi.fn().mockRejectedValue(problem(403, "Out of scope for this resource")));

    await act(async () => {
      result.current.reveal();
    });

    expect(result.current.notice).toEqual({
      tone: "error",
      text: "This lead is not assigned to you, so you cannot reveal its details.",
    });
    expect(clear).not.toHaveBeenCalled();
  });

  it("drops the code's access when the server says the second factor was not there", async () => {
    const { result } = setup(
      vi.fn().mockRejectedValue(problem(403, "Additional authentication required: otp, stepup")),
    );

    await act(async () => {
      result.current.reveal();
    });

    expect(clear).toHaveBeenCalledTimes(1);
    expect(result.current.notice?.tone).toBe("error");
  });

  it("runs the reveal through the step-up, never around it", async () => {
    const { result } = setup(vi.fn().mockResolvedValue("+91 98765 04821"));

    await act(async () => {
      result.current.reveal();
    });

    expect(run).toHaveBeenCalledTimes(1);
  });
});
