import { useRef, useState } from "react";

import { releaseReservation } from "@/api/movements";
import type { ReservationDetailOut, ReservationReleaseIn, ReservationReleaseOut } from "@/api/movementTypes";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { validateRelease } from "@/lib/reservationForm";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { errorProp } from "@/movements/errorProp";
import { useDiscreteUoms } from "@/movements/useMovementReference";
import { FormDialog, RadioGroup, TextField } from "@/ui";

interface ReleaseReservationDialogProps {
  reservation: ReservationDetailOut;
  onClose: () => void;
  onReleased: (released: ReservationReleaseOut) => void;
}

/**
 * `POST /operations/inventory/reservations/{id}:release` — `operations.inventory.stock.reserve`.
 * Gives back all of the open remainder (an empty body) or part of it (a decimal quantity, checked
 * against what remains). The stock returns to available at once.
 */
export function ReleaseReservationDialog({ reservation, onClose, onReleased }: ReleaseReservationDialogProps) {
  const [mode, setMode] = useState<"all" | "part">("all");
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  const quantityRef = useRef<HTMLInputElement>(null);
  const discrete = useDiscreteUoms();
  const keyFor = useSubmissionKeys();

  const release = useMutation(
    (input: ReservationReleaseIn) => releaseReservation(reservation.id, input, keyFor(input)),
    { onSuccess: (released) => onReleased(released) },
  );

  function submit() {
    const result = validateRelease(mode, quantity, reservation.remaining, reservation.uom, discrete);
    if (!result.ok) {
      setError(result.message);
      quantityRef.current?.focus();
      return;
    }
    setError(undefined);
    void release.mutate(result.quantity === null ? {} : { quantity: result.quantity });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Release ${reservation.sku}`}
      description={`${reservation.remaining} ${reservation.uom} is still reserved at ${reservation.location_code}.`}
      submitLabel="Release stock"
      pendingLabel="Releasing…"
      pending={release.pending}
      dirty={mode === "part" && quantity !== ""}
      {...formDialogError(release.error, release.fieldErrors)}
    >
      <RadioGroup
        legend="How much to release"
        name="release-mode"
        value={mode}
        onChange={(v) => setMode(v as "all" | "part")}
        options={[
          {
            value: "all",
            label: `Everything still reserved (${reservation.remaining} ${reservation.uom})`,
            description: "The reservation closes and the stock is available again.",
          },
          { value: "part", label: "Part of it", description: "The rest stays reserved." },
        ]}
      />
      {mode === "part" ? (
        <TextField
          ref={quantityRef}
          label="Quantity to release"
          inputMode="decimal"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          helper={`In ${reservation.uom}, up to 3 decimals, at most ${reservation.remaining}.`}
          required
          {...errorProp(error, release.fieldErrors.quantity)}
        />
      ) : null}
    </FormDialog>
  );
}
