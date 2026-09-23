import { Link } from "react-router-dom";

import type { Brand, SettingField, SettingValue, ThemeTokens } from "@/theme/schema";

import { SettingControl } from "./SettingControl";
import { fontOptions, TOKEN_GROUPS } from "./tokens";
import type { TokenFieldDef } from "./tokens";

/**
 * The store's own tokens — colour, type, shape — and a read-only reminder of the brand above them.
 *
 * **The brand is not editable here.** ADR-0011 moved a store's name, tagline and logo text onto
 * its own identity (`/settings`), so a theme edit can no longer take a merchant's name with it.
 * This panel only reads `brand` — resolved from that identity by whoever loaded the store — to
 * point a merchant who comes looking for it in the right direction.
 *
 * Every token control is the same `SettingControl` the section inspector uses, driven by a
 * `SettingField` built from the token's definition. One control layer, one set of null- and
 * parse-handling rules, and a colour behaves identically whether it is a token or a setting.
 *
 * Changes land on the canvas immediately: the canvas sets `--sf-*` from these values, so there is
 * no preview button and no apply step.
 */

interface ThemeTokenPanelProps {
  tokens: ThemeTokens;
  brand: Brand;
  canEdit: boolean;
  onToken: (key: keyof ThemeTokens, value: string | number) => void;
}

export function ThemeTokenPanel({ tokens, brand, canEdit, onToken }: ThemeTokenPanelProps) {
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
      <section className="flex flex-col gap-3" aria-labelledby="brand-heading">
        <h3 id="brand-heading" className="text-xs font-semibold uppercase tracking-wide text-faint">
          Brand
        </h3>
        <dl className="flex flex-col gap-2">
          <Reading label="Store name" value={brand.name} />
          <Reading label="Tagline" value={brand.tagline} />
        </dl>
        <p className="text-xs text-muted">
          Edit the store's name, tagline and logo text from{" "}
          <Link to="/settings" className="font-semibold text-accent hover:underline">
            Settings
          </Link>
          .
        </p>
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
