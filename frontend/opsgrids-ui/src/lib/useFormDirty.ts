import { useMemo } from "react";

/**
 * True once `current` differs from `initial` — the one comparison a full-page form or a
 * `FormDialog` needs for "unsaved changes": pass the result to `FormDialog`'s `dirty` or to
 * `UnsavedChangesGuard`'s `when`.
 *
 * Compares by value, not by reference, so a form whose fields live in `useState` — a fresh object
 * literal built from them on every render — does not read as dirty merely because the reference
 * changed. The comparison is deep because a form's state is usually a flat record of primitives
 * but occasionally nests (an address, a line item), and a shallow comparison would miss a change
 * underneath. Not a dependency: this compares at most a form's worth of fields, and a full
 * deep-equal library is a lot of bundle for that one job.
 *
 * `initial` must stay the value the form loaded with — captured once, not recomputed from
 * `current` — or nothing is ever dirty. `current` is normally an object literal built fresh each
 * render from the form's own `useState` values; no listener, no subscription, just a comparison
 * on every render, which is cheap for form-sized data.
 */
export function useFormDirty<T>(initial: T, current: T): boolean {
  return useMemo(() => !deepEqual(initial, current), [initial, current]);
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;

  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => deepEqual(item, b[index]));
  }

  const aRecord = a as Record<string, unknown>;
  const bRecord = b as Record<string, unknown>;
  const aKeys = Object.keys(aRecord);
  const bKeys = Object.keys(bRecord);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) => Object.hasOwn(bRecord, key) && deepEqual(aRecord[key], bRecord[key]));
}
