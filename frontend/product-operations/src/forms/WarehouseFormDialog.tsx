import { Suspense, lazy, useRef, useState } from "react";

import { createWarehouse, updateWarehouse } from "@/api/client";
import type { WarehouseIn, WarehouseOut, WarehousePatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { reportOnce } from "@/lib/clientReport";
import { formDialogError } from "@/lib/formError";
import { DEGREES_INPUT_PATTERN, microToFieldText, parsePosition, positionOf } from "@/lib/geo";
import { getMapsApiKey } from "@/lib/mapsConfig";
import type { PlaceSelection } from "@/maps/places";
import { FormDialog, Skeleton, TextField } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";

// The Maps loader stays out of the app's bundle: it is fetched only when this dialog opens in a
// build that has a key.
const PlacesLookup = lazy(() => import("@/maps/PlacesLookup"));

const CODE_PATTERN = /^[A-Za-z0-9_-]+$/;

// Not `inputMode="decimal"`: iOS's decimal keypad has no minus sign, and a site south of the
// equator or west of the prime meridian cannot be typed on it. The text keyboard has one;
// `parsePosition` is what checks what was typed.
const DEGREES_INPUT = {
  inputMode: "text",
  pattern: DEGREES_INPUT_PATTERN,
  autoComplete: "off",
  autoCapitalize: "off",
  autoCorrect: "off",
  spellCheck: false,
} as const;

interface WarehouseFormDialogProps {
  /** `null` creates. */
  warehouse: WarehouseOut | null;
  onClose: () => void;
  onSaved: (warehouse: WarehouseOut) => void;
}

/**
 * Create or edit a warehouse — `POST /operations/warehouses` / `PATCH /operations/warehouses/{id}`.
 * `code` is create-only: it never appears on the patch, and renaming cascades to every inventory
 * item and purchase order that carries the warehouse's name.
 *
 * The site's position is typed in degrees and sent as integer micro-degrees, paired or not at all.
 * With a Maps key, an address lookup fills the location and the position for you; the latitude and
 * longitude fields are always there beside it — the accessible way in, and the only way in a build
 * without a key. A failed lookup never blocks saving.
 */
export function WarehouseFormDialog({ warehouse, onClose, onSaved }: WarehouseFormDialogProps) {
  const [code, setCode] = useState(warehouse?.code ?? "");
  const [name, setName] = useState(warehouse?.name ?? "");
  const [location, setLocation] = useState(warehouse?.location ?? "");
  const [latText, setLatText] = useState(microToFieldText(warehouse?.lat_micro));
  const [lngText, setLngText] = useState(microToFieldText(warehouse?.lng_micro));
  const [latError, setLatError] = useState<string | undefined>(undefined);
  const [lngError, setLngError] = useState<string | undefined>(undefined);
  const [lookupFailed, setLookupFailed] = useState(false);
  const mapsKey = getMapsApiKey();

  // The position the warehouse already has, so an unchanged one is not sent again and a cleared one
  // is sent as an explicit null.
  const stored = warehouse ? positionOf(warehouse) : null;

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `warehouse` never changes under it.
  const dirty =
    code !== (warehouse?.code ?? "") ||
    name !== (warehouse?.name ?? "") ||
    location !== (warehouse?.location ?? "") ||
    latText !== microToFieldText(warehouse?.lat_micro) ||
    lngText !== microToFieldText(warehouse?.lng_micro);

  const save = useMutation(
    (input: WarehouseIn | WarehousePatch) =>
      warehouse
        ? updateWarehouse(warehouse.id, input as WarehousePatch)
        : createWarehouse(input as WarehouseIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function applyPlace(place: PlaceSelection) {
    if (place.address !== "") setLocation(place.address);
    setLatText(microToFieldText(place.lat_micro));
    setLngText(microToFieldText(place.lng_micro));
    setLatError(undefined);
    setLngError(undefined);
  }

  function lookupUnavailable() {
    reportOnce("maps_load_failed", "script_error");
    setLookupFailed(true);
  }

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);
    setLatError(undefined);
    setLngError(undefined);

    const trimmedCode = code.trim();
    if (!warehouse && (trimmedCode.length < 1 || !CODE_PATTERN.test(trimmedCode))) {
      setCodeError("Letters, numbers, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the warehouse a name.");
      return;
    }

    const position = parsePosition(latText, lngText);
    if (position.kind === "invalid") {
      setLatError(position.lat);
      setLngError(position.lng);
      return;
    }

    if (warehouse) {
      const patch: WarehousePatch = { name: trimmedName, location: location.trim() };
      if (position.kind === "position") {
        const unchanged =
          stored !== null && stored.lat_micro === position.lat_micro && stored.lng_micro === position.lng_micro;
        if (!unchanged) {
          patch.lat_micro = position.lat_micro;
          patch.lng_micro = position.lng_micro;
        }
      } else if (stored !== null) {
        // Both fields were cleared: an explicit null for both removes the position.
        patch.lat_micro = null;
        patch.lng_micro = null;
      }
      void save.mutate(patch);
      return;
    }
    void save.mutate({
      code: trimmedCode,
      name: trimmedName,
      location: location.trim(),
      ...(position.kind === "position" ? { lat_micro: position.lat_micro, lng_micro: position.lng_micro } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={warehouse ? `Edit ${warehouse.name}` : "New warehouse"}
      submitLabel={warehouse ? "Save changes" : "Create warehouse"}
      pendingLabel={warehouse ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        disabled={warehouse !== null}
        required
        helper={warehouse ? "A warehouse's code can't change." : "Letters, numbers, - and _."}
        {...((codeError ?? save.fieldErrors.code) !== undefined ? { error: codeError ?? save.fieldErrors.code } : {})}
      />
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        helper="Shown everywhere the warehouse is referenced — inventory, purchase orders."
        {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
      />
      <TextField
        label="Location"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        optional
        {...((save.fieldErrors.location) !== undefined ? { error: save.fieldErrors.location } : {})}
      />
      {mapsKey !== null && !lookupFailed ? (
        <PanelBoundary title="Address lookup" onFail={lookupUnavailable} fallback={() => null}>
          <Suspense fallback={<Skeleton className="h-11 w-full rounded-tile" />}>
            <PlacesLookup apiKey={mapsKey} onPlace={applyPlace} onFailure={lookupUnavailable} />
          </Suspense>
        </PanelBoundary>
      ) : null}
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-1 text-sm font-semibold text-ink">Site position</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label="Latitude"
            value={latText}
            onChange={(e) => setLatText(e.target.value)}
            {...DEGREES_INPUT}
            optional
            helper="Degrees, between -90 and 90. Use a minus sign for south."
            {...((latError ?? save.fieldErrors.lat_micro) !== undefined
              ? { error: latError ?? save.fieldErrors.lat_micro }
              : {})}
          />
          <TextField
            label="Longitude"
            value={lngText}
            onChange={(e) => setLngText(e.target.value)}
            {...DEGREES_INPUT}
            optional
            helper="Degrees, between -180 and 180. Use a minus sign for west."
            {...((lngError ?? save.fieldErrors.lng_micro) !== undefined
              ? { error: lngError ?? save.fieldErrors.lng_micro }
              : {})}
          />
        </div>
        <p className="text-xs text-muted" role="status">
          {mapsKey === null
            ? "Enter the position by hand; the address lookup isn't set up here. Leave both blank if you don't know it."
            : lookupFailed
              ? "The address lookup isn't available right now. Enter the position by hand, or save without it."
              : "Needed to show this warehouse on the map. Leave both blank if you don't know it."}
        </p>
      </fieldset>
    </FormDialog>
  );
}
