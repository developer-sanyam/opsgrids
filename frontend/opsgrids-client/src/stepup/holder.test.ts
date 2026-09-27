/**
 * The holder's one job is to keep a token in memory for a few minutes and then not have it. What
 * matters is everything it refuses to do: write it anywhere that outlives the page, or hand it out
 * after it has run out — even when the timer that should have cleared it never got to run.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearStepUpToken, getStepUpToken, setStepUpToken } from "./holder";

const TOKEN = "step-up-token-value-do-not-store";

/** Every key and value either web storage holds, and every cookie. */
function everythingPersisted(): string {
  const parts: string[] = [];
  for (const store of [localStorage, sessionStorage]) {
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key !== null) parts.push(`${key}=${store.getItem(key) ?? ""}`);
    }
  }
  parts.push(document.cookie);
  return parts.join("|");
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-27T10:00:00Z"));
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  clearStepUpToken();
  vi.useRealTimers();
});

describe("the step-up token holder", () => {
  it("hands back what it was given while the token is live", () => {
    setStepUpToken(TOKEN, 180);
    expect(getStepUpToken()).toBe(TOKEN);
  });

  it("holds nothing until something is set", () => {
    expect(getStepUpToken()).toBeNull();
  });

  it("keeps the token in memory only — nothing reaches web storage or a cookie", () => {
    const sessionWrite = vi.spyOn(Storage.prototype, "setItem");
    setStepUpToken(TOKEN, 180);

    expect(sessionWrite).not.toHaveBeenCalled();
    expect(everythingPersisted()).not.toContain(TOKEN);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("drops the token when its lifetime runs out, by the timer", () => {
    setStepUpToken(TOKEN, 180);

    vi.advanceTimersByTime(174_000);
    expect(getStepUpToken()).toBe(TOKEN);

    // The 5 s safety margin: gone before the server would say so.
    vi.advanceTimersByTime(1_500);
    expect(getStepUpToken()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("drops it on read even when the timer never got to run", () => {
    // A backgrounded tab and a sleeping laptop both suspend timers; the clock still moves.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-27T10:00:00Z"));
    setStepUpToken(TOKEN, 180);

    vi.setSystemTime(new Date("2026-09-27T10:10:00Z"));
    expect(getStepUpToken()).toBeNull();
  });

  it("clears on clear(), and stops the timer with it", () => {
    setStepUpToken(TOKEN, 180);
    expect(vi.getTimerCount()).toBe(1);

    clearStepUpToken();

    expect(getStepUpToken()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("replaces a held token rather than stacking timers", () => {
    setStepUpToken("first", 180);
    setStepUpToken("second", 180);

    expect(getStepUpToken()).toBe("second");
    expect(vi.getTimerCount()).toBe(1);
  });

  it("holds nothing for a lifetime that would not survive the safety margin", () => {
    setStepUpToken(TOKEN, 4);
    expect(getStepUpToken()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("holds nothing for a lifetime that is not a positive number", () => {
    setStepUpToken(TOKEN, 0);
    expect(getStepUpToken()).toBeNull();
    setStepUpToken(TOKEN, Number.NaN);
    expect(getStepUpToken()).toBeNull();
    setStepUpToken(TOKEN, -30);
    expect(getStepUpToken()).toBeNull();
  });
});
