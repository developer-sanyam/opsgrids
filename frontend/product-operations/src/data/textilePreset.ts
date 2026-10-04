import type { ItemAttributeIn } from "@/api/types";
import { checkWholeNumber } from "@/lib/qtyInput";

/** One field of the textile preset. `key` is the attribute key stored on the item. */
export interface PresetField {
  key: string;
  label: string;
  /** `number` is a whole number (GSM, mm) — attributes are not stock, but the no-float rule
   *  still holds (ADR-0014 §2), so the API takes an integer. */
  kind: "number" | "text";
  helper?: string;
}

/**
 * The textile attribute preset (inventory plan, Phase 1): the facts a fabric or garment item
 * carries beyond its ledger bucket. All optional; each one filled in becomes one typed attribute
 * on the item (`PUT /inventory/{id}/attributes/{key}`).
 */
export const TEXTILE_PRESET: readonly PresetField[] = [
  { key: "gsm", label: "GSM", kind: "number", helper: "Grams per square metre, whole number." },
  { key: "width_mm", label: "Width (mm)", kind: "number" },
  { key: "shade", label: "Shade", kind: "text" },
  { key: "composition", label: "Composition", kind: "text", helper: "For example 100% cotton." },
  { key: "count", label: "Count", kind: "text", helper: "Yarn count, for example 40s." },
  { key: "construction", label: "Construction", kind: "text" },
  { key: "finish", label: "Finish", kind: "text" },
  { key: "grade", label: "Grade", kind: "text" },
  { key: "season", label: "Season", kind: "text" },
  { key: "collection", label: "Collection", kind: "text" },
];

const MAX_TEXT = 500;

export interface PresetAttribute {
  key: string;
  input: ItemAttributeIn;
}

export interface PresetResult {
  /** One per field that was filled in and valid. */
  attributes: PresetAttribute[];
  /** `{key: message}` for every field that is not. */
  errors: Record<string, string>;
}

/** Turn the typed preset values into attributes to send, or per-field errors. Blank fields are
 *  skipped: the preset is optional field by field. */
export function buildTextileAttributes(values: Readonly<Record<string, string>>): PresetResult {
  const attributes: PresetAttribute[] = [];
  const errors: Record<string, string> = {};
  for (const field of TEXTILE_PRESET) {
    const raw = (values[field.key] ?? "").trim();
    if (raw === "") continue;
    if (field.kind === "number") {
      const checked = checkWholeNumber(raw, field.label);
      if (checked.ok) attributes.push({ key: field.key, input: { value_type: "number", value_number: checked.value } });
      else errors[field.key] = checked.message;
      continue;
    }
    if (raw.length > MAX_TEXT) {
      errors[field.key] = `${field.label} is at most ${MAX_TEXT} characters.`;
      continue;
    }
    attributes.push({ key: field.key, input: { value_type: "text", value_text: raw } });
  }
  return { attributes, errors };
}
