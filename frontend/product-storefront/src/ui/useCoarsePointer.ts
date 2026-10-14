import { useMediaQuery } from "@opsgrids/ui";

/**
 * True on a device whose main pointer is a finger. The editor uses it to swap keyboard-and-mouse
 * affordances for ones a thumb can use: buttons where a shortcut chord was, and no instructions
 * that name keys the device does not have. Where `matchMedia` does not exist (jsdom) it is false,
 * which keeps the desktop markup the existing tests describe.
 */
export function useCoarsePointer(): boolean {
  return useMediaQuery("(pointer: coarse)", false);
}
