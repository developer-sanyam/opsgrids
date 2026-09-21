import { useId, useRef, useState } from "react";

import { Button, InlineAlert, Select, Switch, TextArea, TextField } from "@opsgrids/ui";

import { ASSET_ACCEPT, isUploadServiceUnavailable, uploadAsset } from "@/api/client";
import type { Category } from "@/api/types";
import type { SettingField, SettingValue } from "@/theme/schema";
import { BASE_PRODUCT_SOURCE_OPTIONS } from "@/theme/sources";
import { resolveImageSrc } from "@/theme/render/assetSrc";

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
  /** The merchant's own categories — what a `product-source` field offers on top of the fixed
   *  "whole catalogue" / "featured" / "in stock" options. Empty until the catalogue loads. */
  categories?: Category[];
}

const HEX = /^#[0-9a-f]{6}$/i;

function asText(value: SettingValue): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

export function SettingControl({
  field,
  value,
  onChange,
  disabled = false,
  categories = [],
}: SettingControlProps) {
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
          // A `product-source` without declared options still has a vocabulary — the fixed
          // "whole catalogue" / "featured" / "in stock" collections, plus every category the
          // merchant's own catalogue actually carries. Falling back to a free-text box here is
          // how a store ends up pointing a grid at a collection that has never existed; falling
          // back to a fixed demo vocabulary is how it ends up pointing at one that never will.
          options={
            field.options ?? [
              ...BASE_PRODUCT_SOURCE_OPTIONS,
              ...categories.map((c) => ({ value: `category:${c.key}`, label: c.label })),
            ]
          }
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

function NumberControl({ field, value, onChange, disabled }: Required<Omit<SettingControlProps, "categories">>) {
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
}: Required<Omit<SettingControlProps, "categories">> & { options: { value: string; label: string }[] }) {
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

function ColorControl({ field, value, onChange, disabled }: Required<Omit<SettingControlProps, "categories">>) {
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

/**
 * An image setting: paste an address, or upload a file.
 *
 * **Two ways to fill one field, on purpose.** Upload is the answer for a merchant who has a photo
 * on their laptop and nowhere to put it; the URL box stays because a merchant who already has a
 * CDN should not be forced through our storage, and removing it would be a regression for every
 * store written before uploads existed. Both write a plain string, and `resolveImageSrc` is what
 * knows the difference at render time.
 *
 * The size and format checks here mirror the server's and do not replace them — the server decides
 * from the decoded bytes (ADR-0009 §5). They exist so the two mistakes people actually make, a
 * 12 MB camera original and an SVG logo, are answered in the same instant they are made rather
 * than after a round trip that was never going to succeed.
 */
function ImageControl({ field, value, onChange, disabled }: Required<Omit<SettingControlProps, "categories">>) {
  const fileInput = useRef<HTMLInputElement>(null);
  const noteId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  const [seen, setSeen] = useState(value);
  // Set the moment a live upload 404s — the known gap: `services/document` isn't deployed on the
  // box. Re-read on every render, not cached in state, so every open control on the page learns
  // it the instant the first one finds out.
  const uploadsUnavailable = isUploadServiceUnavailable();

  // The same "reset when the document moved underneath" shape `useDraft` uses. Without it an undo
  // or a successful upload leaves the preview still saying the *previous* address did not load.
  if (value !== seen) {
    setSeen(value);
    setBroken(false);
  }

  const src = resolveImageSrc(value);
  const typed = typeof value === "string" ? value : "";

  async function receive(file: File | undefined) {
    // Re-picking the same file has to re-fire `change`, so the input is cleared either way.
    if (fileInput.current !== null) fileInput.current.value = "";
    if (file === undefined || busy) return;

    setBusy(true);
    setError(null);
    try {
      const asset = await uploadAsset(file);
      // The root-relative path, never an absolute URL — the host belongs to config (ADR-0009 §6).
      onChange(asset.public_path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The upload failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <TextField
        label={field.label}
        value={typed}
        placeholder="https://"
        onChange={(event) => {
          setError(null);
          onChange(event.target.value === "" ? null : event.target.value);
        }}
        disabled={disabled || busy}
        {...(field.help !== undefined ? { helper: field.help } : {})}
      />

      {uploadsUnavailable ? (
        <InlineAlert tone="info" live="off" title="Uploads aren't available yet">
          Paste an image address above instead. This starts working on its own once the upload
          service is deployed — nothing here needs to change.
        </InlineAlert>
      ) : (
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileInput.current?.click()}
            disabled={disabled || busy}
            aria-describedby={noteId}
          >
            {busy ? "Uploading…" : "Upload"}
          </Button>
          {/* Kept out of the tab order and out of the accessibility tree: the button above is the
              real control, and a focusable element nobody can see is a keyboard trap in miniature. */}
          <input
            ref={fileInput}
            type="file"
            accept={ASSET_ACCEPT}
            tabIndex={-1}
            aria-label={`Upload an image for ${field.label}`}
            className="hidden"
            onChange={(event) => void receive(event.target.files?.[0])}
          />
          {/* Reserved whether or not it has anything to say, so nothing moves when it does. */}
          <p role="status" aria-live="polite" className="min-h-4 text-xs text-muted">
            {busy ? "Uploading…" : null}
          </p>
        </div>
      )}

      <p id={noteId} className="text-xs text-muted">
        JPEG, PNG or WebP, up to 5 MB. SVG files aren&apos;t accepted. An uploaded image can be
        viewed by anyone who has its address.
      </p>

      {error !== null ? (
        <InlineAlert
          tone="danger"
          title="That image wasn't uploaded"
          action={
            <Button variant="ghost" size="sm" onClick={() => setError(null)}>
              Dismiss
            </Button>
          }
        >
          {error}
        </InlineAlert>
      ) : null}

      {/* Not a labelled control — a preview of the address above it, which is why it carries no
          `<label>` and its states say what they mean in words. Fixed height in every state, so
          the inspector does not jump when an image resolves. */}
      <div className="grid h-20 place-items-center overflow-hidden rounded-tile border border-line bg-ground px-3 text-center text-xs text-muted">
        {src === null ? (
          <span>No image — the theme draws its own plate.</span>
        ) : broken ? (
          <span>That image didn&apos;t load. Check the address.</span>
        ) : (
          // Decorative here: the merchant is checking that the address resolves, and the
          // storefront's own alt text belongs to the renderer, not to this preview.
          <img src={src} alt="" className="h-20 w-full object-cover" onError={() => setBroken(true)} />
        )}
      </div>
    </div>
  );
}
