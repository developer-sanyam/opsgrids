/**
 * Unmount what a test rendered before the next one renders. Without it, every `getByRole` in a
 * file searches the accumulated output of every earlier test and starts finding the wrong
 * element — the same reasoning `@opsgrids/ui`'s own `test-setup.ts` states.
 *
 * Pins the clock to `testKit`'s own `NOW` — every fixture in this app (`makeOffer`'s
 * `expires_at`, `makeTask`'s timestamps) is built relative to that one constant, and the offer-
 * liveness check this app renders around (`findMyLiveOffer`, `expires_at > now`) defaults to the
 * *real* wall clock. Without this, a fixture offer that "expires" 5 minutes after `NOW` silently
 * starts reading as already-expired the moment real time passes that point — a real bug this
 * exact way, caught only because it started failing mid-session once the wall clock crossed it.
 * `vi.setSystemTime` matches the convention `product-hrms`/`product-pulseboard`/`opsgrids-ui`
 * already use for the same reason.
 */

import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

import { NOW } from "@/worker/testKit";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
