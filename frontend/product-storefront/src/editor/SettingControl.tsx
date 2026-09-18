import { useState } from "react";

import { Select, Switch, TextArea, TextField } from "@opsgrids/ui";

import type { SettingField, SettingValue } from "@/theme/schema";
import { PRODUCT_SOURCE_OPTIONS } from "@/theme/sources";

/**
 * One control for one `SettingField`, chosen by its `type` and nothing else.
 *
 * The editor never names a section or a setting: this switch is the whole of its knowledge about
 * what a merchant can configure. Add a field to a `SectionDef` and a control appears here; add a
 * new `type` to the schema and this is the one file that needs to learn it.
 *
 * Every branch handles `null`, because `SettingValue` includes it and a theme ships fields that
 * start empty (a hero with no image). A null never reaches an input as `value={null}`.
 */

interface SettingControlProps {
  field: SettingField;
  value: SettingValue;
  onChange: (value: SettingValue) => void;
  /** Read-only rendering is the inspector's job; this only ever disables during a save. */
  disabled?: boolean;
}

const HEX = /^#[0-9a-f]{6}$/i;

function asText(value: SettingValue): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

export function SettingControl({ field, value, onChange, disabled = false }: SettingControlProps) {
  const helper = field.help !== undefined ? { helper: field.help } : {};

  switch (field.type) {
    case "textarea":
      return (
        <TextArea
          label={field.label}
          value={asText(value)}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          rows={3}
          {...helper}
        />
      );

    case "number":
      return <NumberControl field={field} value={value} onChange={onChange} disabled={disabled} />;

    case "select":
      return (
        <SelectControl
          field={field}
          options={field.options ?? []}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );

    case "product-source":
      return (
        <SelectControl
          field={field}
          // A `product-source` without declared options still has a vocabulary: the one the
          // catalogue and the renderer agree on. Falling back to a free-text box here is how a
          // store ends up pointing a grid at a collection that has never existed.
          options={field.options ?? PRODUCT_SOURCE_OPTIONS}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      );

    case "toggle":
      return (
        <Switch
          label={field.label}
          checked={value === true}
          onChange={onChange}
          disabled={disabled}
          {...(field.help !== undefined ? { description: field.help } : {})}
        />
      );

    case "color":
      return <ColorControl field={field} value={value} onChange={onChange} disabled={disabled} />;

    case "image":
      return <ImageControl field={field} value={value} onChange={onChange} disabled={disabled} />;

    case "text":
    default:
      return (
        <TextField
          label={field.label}
          value={asText(value)}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          {...helper}
        />
      );
  }
}

/**
 * A draft string beside the document value.
 *
 * Controls that parse — a number, a hex colour — cannot be driven straight from the document, or
 * clearing the field to retype it would write `null` and a half-typed `#1a1` would write nothing
 * at all. The draft holds what was typed; the document takes only values that parse. When the
 * document changes underneath (undo, redo, discard), the draft resets to match it.
 */
function useDraft(value: SettingValue): [string, (next: string) => void] {
  const [draft, setDraft] = useState(() => asText(value));
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(asText(value));
  }
  return [draft, setDraft];
}

function NumberControl({ field, value, onChange, disabled }: Required<SettingControlProps>) {
  const [draft, setDraft] = useDraft(value);
  const [error, setError] = useState<string | undefined>(undefined);

  function commit(next: string) {
    setDraft(next);
    if (next.trim() === "") {
      setError(undefined);
      onChange(null);
      return;
    }
    const parsed = Number(next);
    if (!Number.isFinite(parsed)) {
      setError("Enter a number.");
      return;
    }
    setError(undefined);
    onChange(parsed);
  }

  return (
    <TextField
      label={field.label}
      type="number"
      inputMode="decimal"
      value={draft}
      onChange={(event) => commit(event.target.value)}
      disabled={disabled}
      {...(field.help !== undefined ? { helper: field.help } : {})}
      {...(error !== undefined ? { error } : {})}
    />
  );
}

function SelectControl({
  field,
  options,
  value,
  onChange,
  disabled,
}: Required<SettingControlProps> & { options: { value: string; label: string }[] }) {
  const current = typeof value === "string" ? value : "";
  // A stored value the theme no longer offers stays visible and selected rather than silently
  // reading as the first option — the merchant chose it, and losing it on open is a data loss.
  const known = options.some((option) => option.value === current);
  const resolved =
    current !== "" && !known
      ? [...options, { value: current, label: `${current} (not in this theme)` }]
      : options;

  return (
    <Select
      label={field.label}
      value={current}
      options={resolved}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      {...(current === "" ? { placeholder: "Not set" } : {})}
      {...(field.help !== undefined ? { helper: field.help } : {})}
    />
  );
}

function ColorControl({ field, value, onChange, disabled }: Required<SettingControlProps>) {
  const [draft, setDraft] = useDraft(value);
  const [error, setError] = useState<string | undefined>(undefined);
  const swatch = HEX.test(draft) ? draft : "#000000";

  function commit(next: string) {
    setDraft(next);
    if (next.trim() === "") {
      setError(undefined);
      onChange(null);
      return;
    }
    if (!HEX.test(next)) {
      setError("Use a six-digit hex colour, like #1a1a1a.");
      return;
    }
    setError(undefined);
    onChange(next);
  }

  return (
    <div className="flex items-end gap-2">
      <TextField
        label={field.label}
        value={draft}
        onChange={(event) => commit(event.target.value)}
        disabled={disabled}
        spellCheck={false}
        containerClassName="flex-1"
        {...(field.help !== undefined ? { helper: field.help } : {})}
        {...(error !== undefined ? { error } : {})}
      />
      <input
        type="color"
        aria-label={`${field.label} — colour picker`}
        value={swatch}
        disabled={disabled}
        onChange={(event) => commit(event.target.value)}
        className="h-11 w-12 shrink-0 cursor-pointer rounded-tile border border-line bg-surface p-1 disabled:cursor-not-allowed"
      />
    </div>
  );
}

function ImageControl({ field, value, onChange, disabled }: Required<SettingControlProps>) {
  const url = typeof value === "string" ? value.trim() : "";
  const [broken, setBroken] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <TextField
        label={field.label}
        value={typeof value === "string" ? value : ""}
        placeholder="https://"
        onChange={(event) => {
          setBroken(false);
          onChange(event.target.value === "" ? null : event.target.value);
        }}
        disabled={disabled}
        {...(field.help !== undefined ? { helper: field.help } : {})}
      />
      {/* Not a labelled control — a preview of the address above it, which is why it carries no
          `<label>` and its states say what they mean in words. */}
      <div className="grid h-20 place-items-center overflow-hidden rounded-tile border border-line bg-ground px-3 text-center text-xs text-muted">
        {url === "" ? (
          <span>No image — the theme draws its own plate.</span>
        ) : broken ? (
          <span>That image didn&apos;t load. Check the address.</span>
        ) : (
          // Decorative here: the merchant is checking that the address resolves, and the
          // storefront's own alt text belongs to the renderer, not to this preview.
          <img src={url} alt="" className="h-20 w-full object-cover" onError={() => setBroken(true)} />
        )}
      </div>
    </div>
  );
}
