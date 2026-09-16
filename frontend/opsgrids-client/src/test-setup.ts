/**
 * Unmount what a test rendered before the next one renders. Testing Library registers this itself
 * only when the runner exposes global hooks, and this suite imports its helpers explicitly.
 */

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);
