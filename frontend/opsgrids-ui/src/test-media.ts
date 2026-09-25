/**
 * A viewport for the tests that render different markup per width (`DataTable`'s cards,
 * `FilterBar`'s sheet). jsdom has no `matchMedia`, which is also what every app's tests see — so
 * leaving it unstubbed is the "desktop, as before" case, and a test stubs it only to be a phone.
 */

import { vi } from "vitest";

export function stubViewport(width: number): void {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min?.[1] !== undefined ? width >= Number(min[1]) : false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    };
  });
}
