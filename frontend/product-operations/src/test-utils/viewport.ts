/**
 * Pretend to be a viewport of `width` CSS pixels for the components that render different markup
 * per width (`DataTable`'s cards below 768px). jsdom has no `matchMedia`, which is also what every
 * other test sees — leaving it unstubbed is the desktop case, and a test stubs it only to be a
 * phone. Undo with `vi.unstubAllGlobals()`.
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
