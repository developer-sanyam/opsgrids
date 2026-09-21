import { Button, EmptyState, InlineAlert, LayersIcon, TextField } from "@opsgrids/ui";

import type { Category } from "@/api/types";
import type { SectionDef, SectionInstance, SettingValue } from "@/theme/schema";

import { COLUMNS, MAX_H, MIN_H, MIN_W } from "./grid";
import { SettingControl } from "./SettingControl";

/**
 * The right-hand pane: everything about the selected section.
 *
 * It is generated entirely from `SectionDef.fields` — the inspector contains no list of section
 * types, no special case for the hero, nothing that must be edited when a theme grows a setting.
 * Position and size are here as four numbers as well as on the canvas, because typing `4` is
 * often the fastest way to place something and is the only way that works without a pointer.
 */

interface SettingsInspectorProps {
  section: SectionInstance | undefined;
  /** `undefined` for a type this build does not know — a placeholder, never a crash. */
  def: SectionDef | undefined;
  /** The merchant's own categories, so a `product-source` control offers what their catalogue
   *  actually carries rather than a fixed demo vocabulary. */
  categories: Category[];
  canEdit: boolean;
  onSettingChange: (key: string, value: SettingValue) => void;
  onMove: (x: number, y: number) => void;
  onResize: (w: number, h: number) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onDepth: (direction: "forward" | "backward") => void;
}

export function SettingsInspector({
  section,
  def,
  categories,
  canEdit,
  onSettingChange,
  onMove,
  onResize,
  onDuplicate,
  onRemove,
  onDepth,
}: SettingsInspectorProps) {
  if (!section) {
    return (
      <EmptyState
        icon={<LayersIcon className="size-6" />}
        title="Nothing selected"
        description="Pick a section on the canvas to change what it says and how it looks."
      />
    );
  }

  const title = def?.name ?? section.type;

  /** An emptied number field is mid-edit, not a request to jump to the minimum. */
  function numberOr(raw: string, fallback: number): number {
    const parsed = Number(raw);
    return raw.trim() === "" || !Number.isFinite(parsed) ? fallback : parsed;
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-bold text-ink">{title}</h2>
        <p className="text-xs text-muted">
          {def ? `${section.layout.w} of ${COLUMNS} columns` : "Unrecognised section"}
        </p>
      </div>

      {!def ? (
        <InlineAlert tone="warning" title="This section isn't in the current theme" live="off">
          It stays on the page and keeps its settings, so nothing is lost. You can move it, copy it
          or delete it — but its settings can only be edited by a build that knows the type
          <span className="font-mono"> {section.type}</span>.
        </InlineAlert>
      ) : null}

      {canEdit ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="text-xs font-semibold uppercase tracking-wide text-faint">
            Position and size
          </legend>
          <div className="grid grid-cols-2 gap-3">
            <TextField
              label="Column"
              type="number"
              min={1}
              max={COLUMNS}
              value={String(section.layout.x + 1)}
              onChange={(event) => onMove(numberOr(event.target.value, section.layout.x + 1) - 1, section.layout.y)}
              helper="1 is the left edge."
            />
            <TextField
              label="Row"
              type="number"
              min={1}
              value={String(section.layout.y + 1)}
              onChange={(event) => onMove(section.layout.x, numberOr(event.target.value, section.layout.y + 1) - 1)}
            />
            <TextField
              label="Width"
              type="number"
              min={MIN_W}
              max={COLUMNS}
              value={String(section.layout.w)}
              onChange={(event) => onResize(numberOr(event.target.value, section.layout.w), section.layout.h)}
              helper={`In columns, 1 to ${COLUMNS}.`}
            />
            <TextField
              label="Height"
              type="number"
              min={MIN_H}
              max={MAX_H}
              value={String(section.layout.h)}
              onChange={(event) => onResize(section.layout.w, numberOr(event.target.value, section.layout.h))}
              helper="In rows of 40px."
            />
          </div>
        </fieldset>
      ) : (
        <p className="text-sm text-muted">
          Column {section.layout.x + 1}, row {section.layout.y + 1} — {section.layout.w} columns
          wide, {section.layout.h} rows tall.
        </p>
      )}

      {def ? (
        <SettingsForm
          section={section}
          def={def}
          canEdit={canEdit}
          onChange={onSettingChange}
          categories={categories}
        />
      ) : (
        <UnknownSettings section={section} />
      )}

      {canEdit ? (
        <div className="flex flex-col gap-2 border-t border-line pt-4">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">
            This section
          </span>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={onDuplicate}>
              Duplicate
            </Button>
            <Button variant="outline" size="sm" onClick={() => onDepth("forward")}>
              Bring forward
            </Button>
            <Button variant="outline" size="sm" onClick={() => onDepth("backward")}>
              Send backward
            </Button>
            <Button variant="danger" size="sm" onClick={onRemove}>
              Delete
            </Button>
          </div>
          <p className="text-xs text-muted">
            Deleting can be undone until you leave the editor.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function SettingsForm({
  section,
  def,
  canEdit,
  onChange,
  categories,
}: {
  section: SectionInstance;
  def: SectionDef;
  canEdit: boolean;
  onChange: (key: string, value: SettingValue) => void;
  categories: Category[];
}) {
  if (def.fields.length === 0) {
    return (
      <p className="text-sm text-muted">
        This section has no settings — its look comes entirely from the theme.
      </p>
    );
  }

  if (!canEdit) {
    return (
      <dl className="flex flex-col gap-3">
        {def.fields.map((field) => (
          <div key={field.key} className="flex flex-col gap-0.5">
            <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
              {field.label}
            </dt>
            <dd className="text-sm text-ink">{readableValue(section.settings[field.key])}</dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {def.fields.map((field) => (
        <SettingControl
          // Keyed by section as well as field: switching selection must rebuild the controls, or
          // a draft half-typed into one section's heading would appear in the next one's.
          key={`${section.id}:${field.key}`}
          field={field}
          // `?? field.default` would be wrong: `null` is a value a merchant chose (a hero with
          // no image), and only an absent key falls back to the declared default.
          value={
            Object.prototype.hasOwnProperty.call(section.settings, field.key)
              ? (section.settings[field.key] ?? null)
              : field.default
          }
          onChange={(value) => onChange(field.key, value)}
          categories={categories}
        />
      ))}
    </div>
  );
}

/** Whatever the document holds for a type this build cannot render controls for. */
function UnknownSettings({ section }: { section: SectionInstance }) {
  const entries = Object.entries(section.settings);
  if (entries.length === 0) {
    return <p className="text-sm text-muted">This section has no stored settings.</p>;
  }
  return (
    <dl className="flex flex-col gap-3">
      {entries.map(([key, value]) => (
        <div key={key} className="flex flex-col gap-0.5">
          <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{key}</dt>
          <dd className="text-sm break-words text-ink">{readableValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function readableValue(value: SettingValue | undefined): string {
  if (value === undefined || value === null || value === "") return "Not set";
  if (typeof value === "boolean") return value ? "On" : "Off";
  return String(value);
}
