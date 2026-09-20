/**
 * Turning a `SectionDef` into a `SectionInstance`.
 *
 * The library itself is `@/theme/sections` — the editor does not keep a second copy of it, and
 * does not know one section type from another. Every control in the inspector is generated from
 * `SectionDef.fields`, so a new field on a def, or a whole new def, is a working control with no
 * editor change. That is the promise behind "configure every component", and
 * `SettingsInspector.test.tsx` pins it with a def the library has never heard of.
 */

import type { SectionDef, SectionInstance, SettingValue } from "@/theme/schema";

/**
 * A def's settings at their declared defaults.
 *
 * `@/theme/sections` exports a `defaultSettings(type)` that resolves the def by type; this one
 * takes the def itself, which is what lets the palette, the reducer and the tests work with a def
 * that is not in the registry (an arbitrary one in a test, a theme-supplied one later).
 */
export function defaultSettingsFor(def: SectionDef): Record<string, SettingValue> {
  const settings: Record<string, SettingValue> = {};
  for (const field of def.fields) settings[field.key] = field.default;
  return settings;
}

/**
 * A new instance of `def`, at `at`.
 *
 * The id is passed in rather than generated here: a reducer that reaches for `crypto.randomUUID`
 * is no longer a pure function, and undo/redo is only testable because this one is.
 */
export function createInstance(
  def: SectionDef,
  id: string,
  at: { x: number; y: number },
): SectionInstance {
  return {
    id,
    type: def.type,
    settings: defaultSettingsFor(def),
    layout: { x: at.x, y: at.y, w: def.defaultLayout.w, h: def.defaultLayout.h },
  };
}

/** The def for a type, or `undefined` — an unknown type is a placeholder, never a crash. */
export function findDef(library: SectionDef[], type: string): SectionDef | undefined {
  return library.find((def) => def.type === type);
}
