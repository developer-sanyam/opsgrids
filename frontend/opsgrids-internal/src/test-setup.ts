/**
 * Unmount what a test rendered before the next one renders. Without it, every `getByRole` in a
 * file searches the accumulated output of every earlier test and starts finding the wrong
 * element — the same reasoning `@opsgrids/ui`'s own `test-setup.ts` states.
 *
 * The session store is module state that outlives a render, so it is cleared here too: a token
 * one test signed in with must not be the token the next one boots from. So are the `fetch` stubs
 * a test installed, and the storage the theme and the token write to.
 */

import { afterEach, vi } from "vitest";
import { cleanup, configure } from "@testing-library/react";

import { configureSession, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { OPERATOR_SESSION_KEY } from "@/auth/keys";

// Fixture mode fakes the network's latency (`settle`) so loading states are visible in a browser.
// A suite has no use for it: every screen would spend a quarter second per read for nothing. What
// a test needs is the loading state on demand, which it asks for by holding a promise open.
vi.mock("@opsgrids/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@opsgrids/client")>();
  return { ...actual, settle: <T>(value: T): Promise<T> => Promise.resolve(value) };
});

// The console names its own session slot before anything can read a token; the suite does the same.
configureSession(OPERATOR_SESSION_KEY);

// The fixtures are loaded on demand in the app (a production build must not carry them), so the
// first read of a file would pay for loading them inside a test's wait. Loading them here, once per
// file and before any test starts, keeps that cost out of every timeout.
await import("@/api/fixtureClient");

// `findBy…` and `waitFor` give up after a second by default, which is less than a lazy route takes
// to load on a busy machine. Five seconds is the same ceiling with room; see `vitest.config.ts`.
configure({ asyncUtilTimeout: 5000 });

// jsdom does not implement scrolling; the shell resets it on every route change.
window.scrollTo = vi.fn();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setAccessToken(null);
  setUnauthorizedHandler(null);
  sessionStorage.clear();
  localStorage.clear();
});
