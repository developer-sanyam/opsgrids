/**
 * `withStepUp` is the policy on its own, with no React and no network: what it holds, when it
 * asks, and what it is careful not to swallow.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { StepUpCancelledError } from "./cancelled";
import { clearStepUpToken, getStepUpToken, setStepUpToken } from "./holder";
import { withStepUp } from "./withStepUp";

const HELD = "held-step-up-token";
const FRESH = "fresh-step-up-token";

function fresh(token = FRESH, expiresIn = 180) {
  return { access_token: token, token_type: "Bearer" as const, expires_in: expiresIn };
}

beforeEach(() => {
  vi.useFakeTimers();
  clearStepUpToken();
});

afterEach(() => {
  clearStepUpToken();
  vi.useRealTimers();
});

describe("withStepUp", () => {
  it("calls run with the held token as a bearer and never asks", async () => {
    setStepUpToken(HELD, 180);
    const run = vi.fn().mockResolvedValue("value");
    const ask = vi.fn();

    await expect(withStepUp(run, ask)).resolves.toBe("value");

    expect(run).toHaveBeenCalledWith({ Authorization: `Bearer ${HELD}` });
    expect(ask).not.toHaveBeenCalled();
  });

  it("asks when nothing is held, holds what it gets, and uses it", async () => {
    const run = vi.fn().mockResolvedValue("value");
    const ask = vi.fn().mockResolvedValue(fresh());

    await withStepUp(run, ask);

    expect(run).toHaveBeenCalledWith({ Authorization: `Bearer ${FRESH}` });
    expect(getStepUpToken()).toBe(FRESH);
  });

  it("asks again when the held token has run out", async () => {
    setStepUpToken(HELD, 180);
    vi.advanceTimersByTime(180_000);
    const run = vi.fn().mockResolvedValue("value");
    const ask = vi.fn().mockResolvedValue(fresh());

    await withStepUp(run, ask);

    expect(ask).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith({ Authorization: `Bearer ${FRESH}` });
  });

  it("forgets a held token the server refuses with a 401, and asks for a fresh one", async () => {
    setStepUpToken(HELD, 180);
    const run = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(401, "Token has expired", null))
      .mockResolvedValueOnce("value");
    const ask = vi.fn().mockResolvedValue(fresh());

    await expect(withStepUp(run, ask)).resolves.toBe("value");

    expect(run.mock.calls.map(([headers]) => (headers as { Authorization: string }).Authorization)).toEqual([
      `Bearer ${HELD}`,
      `Bearer ${FRESH}`,
    ]);
  });

  it("does not treat any other failure of a held token as a reason to ask", async () => {
    setStepUpToken(HELD, 180);
    const refusal = new ApiError(403, "Not permitted", null);
    const run = vi.fn().mockRejectedValue(refusal);
    const ask = vi.fn();

    await expect(withStepUp(run, ask)).rejects.toBe(refusal);

    expect(ask).not.toHaveBeenCalled();
    expect(getStepUpToken()).toBe(HELD);
  });

  it("never calls run when the person gives up on the prompt", async () => {
    const run = vi.fn();
    const ask = vi.fn().mockRejectedValue(new StepUpCancelledError());

    await expect(withStepUp(run, ask)).rejects.toBeInstanceOf(StepUpCancelledError);

    expect(run).not.toHaveBeenCalled();
  });

  it("drops the token straight after use when told not to keep it — on success and on failure", async () => {
    await withStepUp(() => Promise.resolve("value"), () => Promise.resolve(fresh()), { keep: false });
    expect(getStepUpToken()).toBeNull();

    setStepUpToken(HELD, 180);
    await expect(
      withStepUp(() => Promise.reject(new Error("boom")), vi.fn(), { keep: false }),
    ).rejects.toThrow("boom");
    expect(getStepUpToken()).toBeNull();
  });

  it("uses a fresh token once even when it is too short-lived to be held", async () => {
    const run = vi.fn().mockResolvedValue("value");

    await withStepUp(run, () => Promise.resolve(fresh(FRESH, 3)));

    expect(run).toHaveBeenCalledWith({ Authorization: `Bearer ${FRESH}` });
    expect(getStepUpToken()).toBeNull();
  });
});
