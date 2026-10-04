/**
 * Unmount what a test rendered before the next one renders. Without it, every `getByRole` in a
 * file searches the accumulated output of every earlier test and starts finding the wrong
 * element — the same reasoning `@opsgrids/ui`'s own `test-setup.ts` states.
 */

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);

/**
 * react-router's data router builds a `Request` for every navigation and hands it an
 * `AbortSignal`. Under jsdom that signal is jsdom's own class, which Node's built-in `Request`
 * (undici) refuses — "Expected signal to be an instance of AbortSignal" — so on some Node
 * versions a test that clicks a button which navigates never navigates. Nothing in a test ever
 * aborts a navigation, so the signal is dropped.
 */
const NativeRequest = globalThis.Request;
globalThis.Request = class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const withoutSignal: RequestInit = { ...init };
    delete withoutSignal.signal;
    super(input, withoutSignal);
  }
};
