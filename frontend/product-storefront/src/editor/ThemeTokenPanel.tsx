import type { SettingField, SettingValue, StoreDoc, ThemeTokens } from "@/theme/schema";

import { SettingControl } from "./SettingControl";
import { fontOptions, TOKEN_GROUPS } from "./tokens";
import type { TokenFieldDef } from "./tokens";

/**
 * The store's own tokens — colour, type, shape — and the brand line that sits above them.
 *
 * Every control here is the same `SettingControl` the section inspector uses, driven by a
 * `SettingField` built from the token's definition. One control layer, one set of null- and
 * parse-handling rules, and a colour behaves identically whether it is a token or a setting.
 *
 * Changes land on the canvas immediately: the canvas sets `--sf-*` from these values, so there is
 * no preview button and no apply step.
 */

interface ThemeTokenPanelProps {
  tokens: ThemeTokens;
  brand: StoreDoc["brand"];
  canEdit: boolean;
  onToken: (key: keyof ThemeTokens, value: string | number) => void;
  onBrand: (key: keyof StoreDoc["brand"], value: string) => void;
}

const BRAND_FIELDS: { key: keyof StoreDoc["brand"]; field: SettingField }[] = [
  { key: "name", field: { key: "name", label: "Store name", type: "text", default: "" } },
  {
    key: "logoText",
    field: {
      key: "logoText",
      label: "Logo text",
      type: "text",
      default: "",
      help: "Shown in the header. Falls back to the store name when empty.",
    },
  },
  { key: "tagline", field: { key: "tagline", label: "Tagline", type: "textarea", default: "" } },
];

export function ThemeTokenPanel({ tokens, brand, canEdit, onToken, onBrand }: ThemeTokenPanelProps) {
  if (!canEdit) {
    return (
      <dl className="flex flex-col gap-3">
        <Reading label="Store name" value={brand.name} />
        <Reading label="Tagline" value={brand.tagline} />
        <Reading label="Accent" value={tokens.colorAccent} />
        <Reading label="Headings" value={tokens.fontHeading} />
        <Reading label="Corner radius" value={`${tokens.radius}px`} />
      </dl>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-4" aria-labelledby="brand-heading">
        <h3 id="brand-heading" className="text-xs font-semibold uppercase tracking-wide text-faint">
          Brand
        </h3>
        {BRAND_FIELDS.map(({ key, field }) => (
          <SettingControl
            key={key}
            field={field}
            value={brand[key]}
            onChange={(value) => onBrand(key, typeof value === "string" ? value : "")}
          />
        ))}
      </section>

      {TOKEN_GROUPS.map((group) => (
        <section key={group.title} className="flex flex-col gap-4" aria-labelledby={`token-${group.title}`}>
          <h3
            id={`token-${group.title}`}
            className="text-xs font-semibold uppercase tracking-wide text-faint"
          >
            {group.title}
          </h3>
          {group.fields.map((definition) => (
            <SettingControl
              key={definition.key}
              field={fieldFor(definition, tokens)}
              value={tokens[definition.key]}
              onChange={(value) => commit(definition, value, onToken)}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

/** A token definition, expressed as the same `SettingField` a section would declare. */
function fieldFor(definition: TokenFieldDef, tokens: ThemeTokens): SettingField {
  const base = { key: definition.key, label: definition.label };
  const help = definition.help !== undefined ? { help: definition.help } : {};

  if (definition.kind === "color") {
    return { ...base, ...help, type: "color", default: tokens[definition.key] };
  }
  if (definition.kind === "font") {
    return {
      ...base,
      ...help,
      type: "select",
      options: fontOptions(tokens[definition.key]),
      default: tokens[definition.key],
    };
  }
  return {
    ...base,
    type: "number",
    default: tokens[definition.key],
    help: `${definition.help ? `${definition.help} ` : ""}${definition.min}–${definition.max}${definition.unit}.`,
  };
}

/**
 * A token is never absent: clearing the field leaves the last good value in place rather than
 * writing `null` into a document that types these as `string` and `number`.
 */
function commit(
  definition: TokenFieldDef,
  value: SettingValue,
  onToken: (key: keyof ThemeTokens, value: string | number) => void,
) {
  if (value === null) return;
  if (definition.kind === "size") {
    if (typeof value !== "number") return;
    onToken(definition.key, Math.min(definition.max, Math.max(definition.min, Math.round(value))));
    return;
  }
  if (typeof value !== "string") return;
  onToken(definition.key, value);
}

function Reading({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</dt>
      <dd className="text-sm text-ink">{value === "" ? "Not set" : value}</dd>
    </div>
  );
}
