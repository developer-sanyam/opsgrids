import { useState } from "react";

import { requestChallanEway } from "@/api/client";
import type { ChallanOut } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, TextField } from "@/ui";

interface ChallanEwayDialogProps {
  challan: ChallanOut;
  onClose: () => void;
  onRequested: (challan: ChallanOut) => void;
}

/**
 * `POST …/challans/{id}:request-eway` — `operations.jobwork.order.issue:ORG`. Supplies the
 * transport facts and asks finance to consider an e-way bill. This module does not generate the
 * bill — it records that finance was asked; finance owns the portal call.
 */
export function ChallanEwayDialog({ challan, onClose, onRequested }: ChallanEwayDialogProps) {
  const [distance, setDistance] = useState(challan.distance_km ? String(challan.distance_km) : "");
  const [vehicleNo, setVehicleNo] = useState(challan.vehicle_no ?? "");
  const [transporterId, setTransporterId] = useState(challan.transporter_id ?? "");
  const [distanceError, setDistanceError] = useState<string | undefined>(undefined);

  const request = useMutation(
    (input: { distance_km: number; vehicle_no?: string; transporter_id?: string }) =>
      requestChallanEway(challan.id, input),
    { onSuccess: (saved) => onRequested(saved) },
  );

  function submit() {
    setDistanceError(undefined);
    if (!/^\d{1,4}$/.test(distance.trim()) || Number(distance) < 1) {
      setDistanceError("Enter the approximate road distance in whole kilometres.");
      return;
    }
    void request.mutate({
      distance_km: Number(distance),
      ...(vehicleNo.trim() ? { vehicle_no: vehicleNo.trim() } : {}),
      ...(transporterId.trim() ? { transporter_id: transporterId.trim() } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`E-way bill for ${challan.challan_number}`}
      description="Finance raises the bill from these facts; this only records the request."
      submitLabel="Request e-way bill"
      pendingLabel="Requesting…"
      pending={request.pending}
      {...formDialogError(request.error, request.fieldErrors)}
    >
      <TextField
        label="Distance (km)"
        inputMode="numeric"
        value={distance}
        onChange={(e) => setDistance(e.target.value)}
        required
        {...(distanceError !== undefined ? { error: distanceError } : {})}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Vehicle number"
          value={vehicleNo}
          onChange={(e) => setVehicleNo(e.target.value)}
          maxLength={20}
        />
        <TextField
          label="Transporter ID"
          value={transporterId}
          onChange={(e) => setTransporterId(e.target.value)}
          maxLength={30}
        />
      </div>
    </FormDialog>
  );
}
