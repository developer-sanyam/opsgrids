/**
 * Unmount what a test rendered before the next one renders. Without it, every `getByRole` in a
 * file searches the accumulated output of every earlier test and starts finding the wrong
 * element — the same reasoning `@opsgrids/ui`'s own `test-setup.ts` states.
 */

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);
