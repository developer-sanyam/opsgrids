/**
 * Unmount what a test rendered before the next one renders.
 *
 * Testing Library registers this itself only when a test runner exposes global hooks; this suite
 * imports `describe`/`it`/`expect` explicitly (as the encoder's tests always have), so the
 * teardown is wired here instead. Without it every `getByRole` searches the accumulated output of
 * every earlier test in the file and starts finding the wrong element.
 */

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);
