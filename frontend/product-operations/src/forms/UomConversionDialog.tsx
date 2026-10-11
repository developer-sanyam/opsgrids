import { useRef, useState } from "react";

import { createUomConversion, updateUomConversion } from "@/api/client";
import type { UomConversionOut } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, TextField } from "@/ui";

interface UomConversionDialogProps {
  itemId: string;
  /** The item's own stock unit — what a container converts into. */
  stockUom: string;
  /** `null` adds one; an existing conversion changes only its ratio (the unit is its identity). */
  conversion: UomConversionOut | null;
  onClose: () => void;
  onSaved: (conversion: UomConversionOut) => void;
}

const RATIO_MAX = 1_000_000_000;

function parseRatio(value: string): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1 && n <= RATIO_MAX ? n : null;
}

/**
 * Add or change one unit conversion — `1 [box] = [12] [pcs]` — via
 * `POST /operations/inventory/{id}/uom-conversions` or `PATCH /operations/inventory/uom-conversions/{id}`.
 * The ratio is two whole numbers so it is exact; a decimal would round. A receipt already posted keeps
 * the quantity it was converted to: only later receipts see a changed ratio.
 */
export function UomConversionDialog({ itemId, stockUom, conversion, onClose, onSaved }: UomConversionDialogProps) {
  const [uom, setUom] = useState(conversion?.uom ?? "");
  const [numerator, setNumerator] = useState(conversion ? String(conversion.numerator) : "");
  const [denominator, setDenominator] = useState(conversion ? String(conversion.denominator) : "1");

  const [uomError, setUomError] = useState<string | undefined>(undefined);
  const [numeratorError, setNumeratorError] = useState<string | undefined>(undefined);
  const [denominatorError, setDenominatorError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const dirty =
    uom !== (conversion?.uom ?? "") ||
    numerator !== (conversion ? String(conversion.numerator) : "") ||
    denominator !== (conversion ? String(conversion.denominator) : "1");

  const save = useMutation(
    (input: { uom: string; numerator: number; denominator: number }) =>
      conversion
        ? updateUomConversion(conversion.id, { numerator: input.numerator, denominator: input.denominator })
        : createUomConversion(itemId, input),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setUomError(undefined);
    setNumeratorError(undefined);
    setDenominatorError(undefined);

    const unit = uom.trim();
    if (unit === "" || unit.length > 20) {
      setUomError("Name the container unit, like box or carton.");
      firstFieldRef.current?.focus();
      return;
    }
    const n = parseRatio(numerator);
    if (n === null) {
      setNumeratorError("Enter a whole number, 1 or more.");
      return;
    }
    const d = parseRatio(denominator);
    if (d === null) {
      setDenominatorError("Enter a whole number, 1 or more.");
      return;
    }
    void save.mutate({ uom: unit, numerator: n, denominator: d });
  }

  const n = parseRatio(numerator);
  const d = parseRatio(denominator);
  const preview =
    n !== null && d !== null
      ? `1 ${uom.trim() || "container"} = ${d === 1 ? n : `${n}/${d}`} ${stockUom}`
      : `1 ${uom.trim() || "container"} = ? ${stockUom}`;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={conversion ? `Change the ${conversion.uom} conversion` : "Add a unit conversion"}
      description={`How many ${stockUom} one container holds, so a delivery can be entered in boxes and booked in ${stockUom}.`}
      submitLabel={conversion ? "Save conversion" : "Add conversion"}
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Container unit"
        value={uom}
        onChange={(e) => setUom(e.target.value)}
        disabled={conversion !== null}
        maxLength={20}
        placeholder="box"
        required={conversion === null}
        {...((uomError ?? save.fieldErrors.uom) !== undefined ? { error: uomError ?? save.fieldErrors.uom } : {})}
      />
      <TextField
        label={`Number of ${stockUom}`}
        inputMode="numeric"
        value={numerator}
        onChange={(e) => setNumerator(e.target.value)}
        required
        {...((numeratorError ?? save.fieldErrors.numerator) !== undefined
          ? { error: numeratorError ?? save.fieldErrors.numerator }
          : {})}
      />
      <TextField
        label="Divided by"
        inputMode="numeric"
        value={denominator}
        onChange={(e) => setDenominator(e.target.value)}
        optional
        helper="Leave at 1 unless a container holds a fraction of a unit."
        {...((denominatorError ?? save.fieldErrors.denominator) !== undefined
          ? { error: denominatorError ?? save.fieldErrors.denominator }
          : {})}
      />
      <p className="tnum rounded-tile bg-ground px-4 py-3 text-sm font-semibold text-ink" aria-live="polite">
        {preview}
      </p>
    </FormDialog>
  );
}
