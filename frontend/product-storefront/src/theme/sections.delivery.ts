/**
 * The delivery half of the section library — one block, and the settings behind the buyer's
 * serviceability check.
 *
 * Its own file for the same reason the retail four have theirs: `sections.ts` is the file every
 * other change to the library touches, and a definition landing in the middle of it is a merge
 * conflict with whoever is editing the ones already there. `sections.ts` merges it in on one line.
 *
 * Two contracts it obeys, both from the library's own rules:
 *
 * - **Flat settings.** `SettingValue` is `string | number | boolean | null`, so nothing here is a
 *   list. The zone map — postal codes, modes, promises, fees — is *server* data behind the
 *   merchant's admin (ADR-0010 §9), not settings on a section. A merchant who could type a
 *   delivery promise into a section would be back where this feature started: a store telling
 *   every buyer in the country it delivers in eleven minutes because that is the string in the box.
 * - **No ninth field type.** A text field, a toggle and a colour, all of which the editor already
 *   draws.
 */

import { BLEED_SETTING } from "./schema";
import type { SectionDef } from "./schema";

export const DELIVERY_SECTION_DEFS: SectionDef[] = [
  {
    /*
     * `layout`, not `commerce`: a merchant reaches for this in the same breath as the announcement
     * bar it sits under, and it is chrome that repeats on every page rather than a block of
     * content that belongs to one.
     */
    type: "delivery-check",
    name: "Delivery check",
    group: "layout",
    defaultLayout: { w: 12, h: 3 },
    fields: [
      {
        key: "storeSlug",
        label: "Store handle",
        type: "text",
        default: "",
        help: "Your store's published handle — the name in your storefront address. The check names it when it asks, so it works the same on your own domain as on ours. Leave it empty and the block shows nothing to a buyer.",
      },
      {
        key: "idlePromiseText",
        label: "Promise before a buyer checks",
        type: "text",
        default: "Delivery in 11 minutes",
        help: "Your own words, shown until a buyer gives a PIN code. After that their zone's real answer replaces it — the merchant's copy is the default, the zone's answer is the truth.",
      },
      {
        key: "promptText",
        label: "Prompt",
        type: "text",
        default: "Check your PIN code for the promise where you are.",
        help: "Shown under the promise until a buyer has asked. Say what the box is for, not what the store claims.",
      },
      {
        key: "fieldLabel",
        label: "Field name",
        type: "text",
        default: "PIN code",
        help: "The control's name, read out by a screen reader. A description, not a placeholder.",
      },
      { key: "placeholder", label: "Field placeholder", type: "text", default: "400072" },
      { key: "submitLabel", label: "Button", type: "text", default: "Check" },
      {
        key: "tint",
        label: "Background tint",
        type: "color",
        default: null,
        help: "Leave empty for the theme's surface colour.",
      },
      {
        key: BLEED_SETTING,
        label: "Full width",
        type: "toggle",
        default: true,
        help: "Span the whole canvas instead of the content column.",
      },
    ],
  },
];
