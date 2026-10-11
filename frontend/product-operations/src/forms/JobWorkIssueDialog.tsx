import { useState } from "react";

import { issueJobWorkOrder } from "@/api/client";
import type { JobWorkIssueIn, JobWorkOrderDetailOut } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { stockShortfalls } from "@/lib/orderErrors";
import { formatMoneyPrecise } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { FormDialog, InlineAlert, TextArea, TextField } from "@/ui";

interface JobWorkIssueDialogProps {
  order: JobWorkOrderDetailOut;
  onClose: () => void;
  onIssued: (order: JobWorkOrderDetailOut) => void;
}

/**
 * `POST …:issue` — `operations.jobwork.order.issue:ORG`. Moves every material's stock out of
 * Available into the vendor's bucket and cuts the numbered delivery challan in one transaction.
 * The transport facts are the carrier's, not derivable, and feed the e-way bill if the consignment
 * is worth one. A 409 here is usually "not enough available stock" and names the figures.
 */
export function JobWorkIssueDialog({ order, onClose, onIssued }: JobWorkIssueDialogProps) {
  const [vehicleNo, setVehicleNo] = useState("");
  const [transporterId, setTransporterId] = useState("");
  const [distance, setDistance] = useState("");
  const [note, setNote] = useState("");
  const [distanceError, setDistanceError] = useState<string | undefined>(undefined);
  const keyFor = useSubmissionKeys();

  const dirty = vehicleNo !== "" || transporterId !== "" || distance !== "" || note !== "";

  const issue = useMutation(
    (input: JobWorkIssueIn) => issueJobWorkOrder(order.id, input, keyFor(input)),
    { onSuccess: (issued) => onIssued(issued) },
  );

  const shortfalls = stockShortfalls(issue.error);

  function submit() {
    setDistanceError(undefined);
    const km = distance.trim();
    if (km !== "" && !/^\d{1,4}$/.test(km)) {
      setDistanceError("Whole kilometres, e.g. 120.");
      return;
    }
    void issue.mutate({
      ...(vehicleNo.trim() ? { vehicle_no: vehicleNo.trim() } : {}),
      ...(transporterId.trim() ? { transporter_id: transporterId.trim() } : {}),
      ...(km !== "" ? { distance_km: Number(km) } : {}),
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Issue ${order.number} to ${order.supplier_name || "the vendor"}`}
      description="Moves the stock to the vendor and cuts the delivery challan. This can't be undone — only receiving the material back reverses it."
      submitLabel="Issue material"
      pendingLabel="Issuing…"
      pending={issue.pending}
      dirty={dirty}
      {...(shortfalls.length > 0 ? {} : formDialogError(issue.error, issue.fieldErrors))}
    >
      <ul className="flex flex-col gap-1 rounded-tile border border-line p-3 text-sm" aria-label="Material to issue">
        {order.materials.map((m) => (
          <li key={m.id} className="flex items-center justify-between gap-3">
            <span className="font-medium text-ink">{m.sku}</span>
            <span className="tnum text-muted">
              {formatQty(m.quantity)} {m.uom}
              {m.unit_value_minor > 0 ? ` · ${formatMoneyPrecise(m.unit_value_minor, m.currency)} each` : ""}
            </span>
          </li>
        ))}
      </ul>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Vehicle number"
          value={vehicleNo}
          onChange={(e) => setVehicleNo(e.target.value)}
          placeholder="MH12AB1234"
          maxLength={20}
        />
        <TextField
          label="Transporter ID"
          value={transporterId}
          onChange={(e) => setTransporterId(e.target.value)}
          maxLength={30}
        />
      </div>
      <TextField
        label="Distance (km)"
        inputMode="numeric"
        value={distance}
        onChange={(e) => setDistance(e.target.value)}
        {...(distanceError !== undefined ? { error: distanceError } : {})}
      />
      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
      {shortfalls.length > 0 ? (
        <InlineAlert tone="danger" title="Not enough available stock to issue this order">
          <ul className="mt-1 flex flex-col gap-1">
            {shortfalls.map((sf) => (
              <li key={sf.sku} className="tnum">
                {sf.sku}: needs {sf.requested}
                {sf.uom ? ` ${sf.uom}` : ""}, only {sf.available} available ({sf.on_hand} on hand,{" "}
                {sf.reserved} reserved)
              </li>
            ))}
          </ul>
        </InlineAlert>
      ) : null}
    </FormDialog>
  );
}
