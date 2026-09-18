/**
 * Two helpers the theme files use, so a theme reads as a composition rather than as a wall of
 * settings objects.
 *
 * `sec` merges the section library's declared defaults under the theme's overrides. That matters:
 * a theme written today keeps rendering when a field is added to a section tomorrow, because it
 * picks up the new field's default instead of carrying an undefined key into a `StoreDoc`.
 */

import type { PageDoc, SectionInstance, SettingValue } from "../schema";
import { defaultSettings } from "../sections";

export function sec(
  id: string,
  type: string,
  layout: SectionInstance["layout"],
  overrides: Record<string, SettingValue> = {},
): SectionInstance {
  return {
    id,
    type,
    settings: { ...defaultSettings(type), ...overrides },
    layout,
  };
}

export function page(id: string, route: string, name: string, sections: SectionInstance[]): PageDoc {
  return { id, route, name, sections };
}
