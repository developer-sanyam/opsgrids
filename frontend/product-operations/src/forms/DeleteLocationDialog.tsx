import { deleteLocation } from "@/api/tracking";
import type { LocationOut } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { useIdempotencyKey } from "@/lib/idempotency";
import { ConfirmDialog } from "@/ui";

interface DeleteLocationDialogProps {
  location: LocationOut;
  onClose: () => void;
  onDeleted: (location: LocationOut) => void;
}

/**
 * Delete a location — `DELETE /operations/locations/{id}` (`operations.inventory.location.write`).
 *
 * The server refuses with a 409 and the reason: the location holds stock, has ever held stock
 * (its history is kept), is the warehouse's default bin, has locations inside it, or is an item's
 * home bin. That sentence is the whole answer, so it is shown verbatim in the dialog — which stays
 * open, because the person may want to read it, and there is nothing a retry of the same request
 * would change.
 */
export function DeleteLocationDialog({ location, onClose, onDeleted }: DeleteLocationDialogProps) {
  const keyFor = useIdempotencyKey();
  const remove = useMutation(() => deleteLocation(location.id, keyFor(location.id)), {
    onSuccess: () => onDeleted(location),
  });
  return (
    <ConfirmDialog
      open
      onClose={() => {
        remove.reset();
        onClose();
      }}
      onConfirm={() => void remove.mutate(undefined)}
      title="Delete this location?"
      body={`${location.code} — ${location.name} is removed from the warehouse. A location that holds stock, has ever held stock, or has locations inside it can't be deleted.`}
      confirmLabel="Delete location"
      pendingLabel="Deleting…"
      pending={remove.pending}
      {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      {...(remove.error instanceof ApiError && remove.error.problem?.request_id
        ? { reference: remove.error.problem.request_id }
        : {})}
    />
  );
}
