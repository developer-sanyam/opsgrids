import { useRef, useState } from "react";

import { createLocation, updateLocation } from "@/api/client";
import type { LocationIn, LocationKind, LocationOut, LocationPatch } from "@/api/types";
import { LOCATION_KIND_OPTIONS } from "@/data/locationKinds";
import { ApiError, useCan, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, FormSection, Select, TextField } from "@/ui";
import type { SelectOption } from "@/ui";
import { useToast } from "@opsgrids/ui";

const CODE_PATTERN = /^[A-Za-z0-9_-]+$/;

/** Empty parses as "not set" (`null`) rather than 0 — a capacity or a pick sequence the location
 *  simply doesn't carry, distinct from a real zero. */
function parseOptionalInt(input: string): { ok: true; value: number | null } | { ok: false; message: string } {
  const cleaned = input.trim();
  if (cleaned === "") return { ok: true, value: null };
  if (!/^\d+$/.test(cleaned)) return { ok: false, message: "Enter a whole number." };
  const value = Number(cleaned);
  if (!Number.isSafeInteger(value)) return { ok: false, message: "That number is too large." };
  return { ok: true, value };
}

interface LocationFormDialogProps {
  warehouseId: string;
  /** `null` creates. */
  location: LocationOut | null;
  /**
   * Every other location in the warehouse, as `Select` options for "Parent location" — indented
   * to show the tree. Only read while creating; F1 does not re-parent an existing location (the
   * plan defers tree editing past this phase).
   */
  parentOptions: SelectOption[];
  /** Preselects "Parent location" while creating — the tree's own "Add child" action opens this
   *  dialog with the row it was invoked on already chosen. Ignored while editing. */
  initialParentId?: string | null;
  onClose: () => void;
  onSaved: (location: LocationOut) => void;
}

/**
 * Create or edit one node of a warehouse's location tree — `POST
 * /operations/warehouses/{warehouse_id}/locations` / `PATCH /operations/locations/{id}`.
 *
 * `kind` and the three capacity fields (max weight, max volume, storage type) are disabled unless
 * the caller also holds `operations.inventory.layout.publish` — the API guards those specifically
 * once a location already has stock history, and a control that is present but refuses is a
 * puzzle (`RowActions`' own rule, applied here to fields instead of a whole action). They stay
 * editable on create: a location with no stock history yet has nothing for that guard to protect.
 */
export function LocationFormDialog({
  warehouseId,
  location,
  parentOptions,
  initialParentId,
  onClose,
  onSaved,
}: LocationFormDialogProps) {
  const toast = useToast();
  const canPublish = useCan("operations.inventory.layout.publish", "ORG");
  const guardedEditable = location === null || canPublish;

  const [kind, setKind] = useState<LocationKind | "">(location?.kind ?? "");
  const [code, setCode] = useState(location?.code ?? "");
  const [name, setName] = useState(location?.name ?? "");
  const [parentId, setParentId] = useState(location?.parent_id ?? initialParentId ?? "");
  const [barcode, setBarcode] = useState(location?.barcode ?? "");
  const [pickSequence, setPickSequence] = useState(
    location?.pick_sequence !== null && location?.pick_sequence !== undefined
      ? String(location.pick_sequence)
      : "",
  );
  const [maxWeightGrams, setMaxWeightGrams] = useState(
    location?.max_weight_grams !== null && location?.max_weight_grams !== undefined
      ? String(location.max_weight_grams)
      : "",
  );
  const [maxVolumeMilli, setMaxVolumeMilli] = useState(
    location?.max_volume_milli !== null && location?.max_volume_milli !== undefined
      ? String(location.max_volume_milli)
      : "",
  );
  const [storageType, setStorageType] = useState(location?.storage_type ?? "");

  const [kindError, setKindError] = useState<string | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [barcodeError, setBarcodeError] = useState<string | undefined>(undefined);
  const [pickSequenceError, setPickSequenceError] = useState<string | undefined>(undefined);
  const [maxWeightError, setMaxWeightError] = useState<string | undefined>(undefined);
  const [maxVolumeError, setMaxVolumeError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const dirty =
    kind !== (location?.kind ?? "") ||
    code !== (location?.code ?? "") ||
    name !== (location?.name ?? "") ||
    (!location && parentId !== (initialParentId ?? "")) ||
    barcode !== (location?.barcode ?? "") ||
    pickSequence !== (location?.pick_sequence !== null && location?.pick_sequence !== undefined ? String(location.pick_sequence) : "") ||
    maxWeightGrams !== (location?.max_weight_grams !== null && location?.max_weight_grams !== undefined ? String(location.max_weight_grams) : "") ||
    maxVolumeMilli !== (location?.max_volume_milli !== null && location?.max_volume_milli !== undefined ? String(location.max_volume_milli) : "") ||
    storageType !== (location?.storage_type ?? "");

  const save = useMutation(
    (input: LocationIn | LocationPatch) =>
      location
        ? updateLocation(location.id, input as LocationPatch)
        : createLocation(warehouseId, input as LocationIn),
    {
      onSuccess: (saved) => onSaved(saved),
      onError: (error, input) => {
        const touchedGuarded =
          "kind" in input || "max_weight_grams" in input || "max_volume_milli" in input || "storage_type" in input;
        if (error instanceof ApiError && error.status === 403 && touchedGuarded) {
          toast.error("You don't have permission to change this location's kind or capacity.");
        }
      },
    },
  );

  function validate(): boolean {
    setKindError(undefined);
    setCodeError(undefined);
    setNameError(undefined);
    setBarcodeError(undefined);
    setPickSequenceError(undefined);
    setMaxWeightError(undefined);
    setMaxVolumeError(undefined);

    if (kind === "") {
      setKindError("Choose a kind.");
      return false;
    }
    const trimmedCode = code.trim();
    if (trimmedCode.length < 1 || !CODE_PATTERN.test(trimmedCode)) {
      setCodeError("Letters, numbers, hyphens and underscores only.");
      return false;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the location a name.");
      return false;
    }
    if (barcode.trim().length > 0 && barcode.trim().length < 3) {
      setBarcodeError("A barcode needs at least 3 characters.");
      return false;
    }
    const pickSeq = parseOptionalInt(pickSequence);
    if (!pickSeq.ok) {
      setPickSequenceError(pickSeq.message);
      return false;
    }
    const maxWeight = parseOptionalInt(maxWeightGrams);
    if (!maxWeight.ok) {
      setMaxWeightError(maxWeight.message);
      return false;
    }
    const maxVolume = parseOptionalInt(maxVolumeMilli);
    if (!maxVolume.ok) {
      setMaxVolumeError(maxVolume.message);
      return false;
    }
    return true;
  }

  function submit() {
    if (!validate()) return;
    if (kind === "") return; // narrowing only — `validate` above already refused this

    const trimmedCode = code.trim();
    const trimmedName = name.trim();
    const trimmedBarcode = barcode.trim();
    const pickSeq = parseOptionalInt(pickSequence);
    const maxWeight = parseOptionalInt(maxWeightGrams);
    const maxVolume = parseOptionalInt(maxVolumeMilli);
    const trimmedStorageType = storageType.trim();
    const pickSeqValue = pickSeq.ok ? pickSeq.value : null;
    const maxWeightValue = maxWeight.ok ? maxWeight.value : null;
    const maxVolumeValue = maxVolume.ok ? maxVolume.value : null;

    if (location) {
      const patch: LocationPatch = {
        code: trimmedCode,
        name: trimmedName,
        barcode: trimmedBarcode === "" ? null : trimmedBarcode,
        pick_sequence: pickSeqValue,
        // Guarded fields: only sent when this dialog lets them be edited, and only when they
        // actually changed — an unpublishable no-op patch never reaches the guard at all.
        ...(guardedEditable && kind !== location.kind ? { kind } : {}),
        ...(guardedEditable && maxWeightValue !== location.max_weight_grams
          ? { max_weight_grams: maxWeightValue }
          : {}),
        ...(guardedEditable && maxVolumeValue !== location.max_volume_milli
          ? { max_volume_milli: maxVolumeValue }
          : {}),
        ...(guardedEditable && trimmedStorageType !== (location.storage_type ?? "")
          ? { storage_type: trimmedStorageType === "" ? null : trimmedStorageType }
          : {}),
      };
      void save.mutate(patch);
      return;
    }

    const input: LocationIn = {
      kind,
      code: trimmedCode,
      name: trimmedName,
      ...(parentId ? { parent_id: parentId } : {}),
      ...(trimmedBarcode ? { barcode: trimmedBarcode } : {}),
      ...(pickSeqValue !== null ? { pick_sequence: pickSeqValue } : {}),
      ...(maxWeightValue !== null ? { max_weight_grams: maxWeightValue } : {}),
      ...(maxVolumeValue !== null ? { max_volume_milli: maxVolumeValue } : {}),
      ...(trimmedStorageType ? { storage_type: trimmedStorageType } : {}),
    };
    void save.mutate(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={location ? `Edit ${location.name}` : "New location"}
      submitLabel={location ? "Save changes" : "Create location"}
      pendingLabel={location ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <FormSection title="Identity" headingLevel={3}>
        <Select
          ref={firstFieldRef}
          label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as LocationKind)}
          placeholder="Choose a kind"
          options={LOCATION_KIND_OPTIONS}
          disabled={!guardedEditable}
          required
          {...(!guardedEditable
            ? { helper: "Changing kind needs the layout-publish permission." }
            : {})}
          {...((kindError ?? save.fieldErrors.kind) !== undefined ? { error: kindError ?? save.fieldErrors.kind } : {})}
        />
        <TextField
          label="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
          helper="Letters, numbers, - and _. Unique within this warehouse."
          {...((codeError ?? save.fieldErrors.code) !== undefined ? { error: codeError ?? save.fieldErrors.code } : {})}
        />
        <TextField
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
        />
        {location ? null : (
          <Select
            label="Parent location"
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
            placeholder="No parent — top of the warehouse"
            options={parentOptions}
            helper="Where this sits in the tree — a bin under a shelf, a shelf under a rack, and so on."
          />
        )}
      </FormSection>

      <FormSection
        title="Barcode & pick sequence"
        description="Optional. The barcode a scan resolves to this exact location; the sequence orders a picker's walk through its zone."
        headingLevel={3}
      >
        <TextField
          label="Barcode"
          value={barcode}
          onChange={(e) => setBarcode(e.target.value)}
          optional
          {...((barcodeError ?? save.fieldErrors.barcode) !== undefined ? { error: barcodeError ?? save.fieldErrors.barcode } : {})}
        />
        <TextField
          label="Pick sequence"
          inputMode="numeric"
          value={pickSequence}
          onChange={(e) => setPickSequence(e.target.value)}
          optional
          {...((pickSequenceError ?? save.fieldErrors.pick_sequence) !== undefined
            ? { error: pickSequenceError ?? save.fieldErrors.pick_sequence }
            : {})}
        />
      </FormSection>

      <FormSection
        title="Capacity"
        description={
          guardedEditable
            ? "Optional. What this location can physically hold."
            : "Changing capacity needs the layout-publish permission."
        }
        headingLevel={3}
      >
        <TextField
          label="Max weight (grams)"
          inputMode="numeric"
          value={maxWeightGrams}
          onChange={(e) => setMaxWeightGrams(e.target.value)}
          disabled={!guardedEditable}
          optional
          {...((maxWeightError ?? save.fieldErrors.max_weight_grams) !== undefined
            ? { error: maxWeightError ?? save.fieldErrors.max_weight_grams }
            : {})}
        />
        <TextField
          label="Max volume (millilitres)"
          inputMode="numeric"
          value={maxVolumeMilli}
          onChange={(e) => setMaxVolumeMilli(e.target.value)}
          disabled={!guardedEditable}
          optional
          {...((maxVolumeError ?? save.fieldErrors.max_volume_milli) !== undefined
            ? { error: maxVolumeError ?? save.fieldErrors.max_volume_milli }
            : {})}
        />
        <TextField
          label="Storage type"
          value={storageType}
          onChange={(e) => setStorageType(e.target.value)}
          disabled={!guardedEditable}
          optional
          placeholder="pallet, shelf, bulk…"
          {...((save.fieldErrors.storage_type) !== undefined ? { error: save.fieldErrors.storage_type } : {})}
        />
      </FormSection>
    </FormDialog>
  );
}
