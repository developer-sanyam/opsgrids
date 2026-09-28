import { useState } from "react";

import { listWarehouses, openCountSession } from "@/api/client";
import type { CountScopeKind, CountSessionOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, RadioGroup, Select, TextArea, TextField } from "@/ui";

interface OpenCountSessionDialogProps {
  onClose: () => void;
  onOpened: (session: CountSessionOut) => void;
}

const SCOPE_OPTIONS: { value: CountScopeKind; label: string; description: string }[] = [
  { value: "WAREHOUSE", label: "Whole warehouse", description: "Every location currently holding stock." },
  { value: "ZONE", label: "One zone", description: "A zone location and everything under it." },
  { value: "LOCATIONS", label: "Specific locations", description: "An explicit list of location ids." },
];

/**
 * `POST /operations/counting/sessions` — `operations.inventory.count.manage:ORG` (ADR-0026).
 * Refused (409) while the chosen warehouse already has a session `ACTIVE` or under `REVIEW` — at
 * most one count in flight per warehouse (ADR-0026 §5), surfaced here as an ordinary form error
 * rather than a pre-check, since the exclusivity lock is the server's, not this dialog's.
 */
export function OpenCountSessionDialog({ onClose, onOpened }: OpenCountSessionDialogProps) {
  const [warehouseId, setWarehouseId] = useState("");
  const [scopeKind, setScopeKind] = useState<CountScopeKind>("WAREHOUSE");
  const [zoneLocationId, setZoneLocationId] = useState("");
  const [locationIdsText, setLocationIdsText] = useState("");
  const [label, setLabel] = useState("");
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouseOptions =
    warehousesState.status === "success"
      ? warehousesState.data.items.map((w) => ({ value: w.id, label: `${w.code} · ${w.name}` }))
      : [];

  const open = useMutation(
    (input: {
      warehouse_id: string;
      scope_kind: CountScopeKind;
      zone_location_id?: string;
      location_ids?: string[];
      label?: string;
    }) => openCountSession(input),
    { onSuccess: (saved) => onOpened(saved) },
  );

  function submit() {
    setFieldError(undefined);
    if (!warehouseId) {
      setFieldError("Choose which warehouse to count.");
      return;
    }
    if (scopeKind === "ZONE" && zoneLocationId.trim().length === 0) {
      setFieldError("Give the zone location's id.");
      return;
    }
    const locationIds =
      scopeKind === "LOCATIONS"
        ? locationIdsText
            .split(/[\s,]+/)
            .map((v) => v.trim())
            .filter(Boolean)
        : undefined;
    if (scopeKind === "LOCATIONS" && (!locationIds || locationIds.length === 0)) {
      setFieldError("List at least one location id.");
      return;
    }
    void open.mutate({
      warehouse_id: warehouseId,
      scope_kind: scopeKind,
      ...(scopeKind === "ZONE" ? { zone_location_id: zoneLocationId.trim() } : {}),
      ...(locationIds ? { location_ids: locationIds } : {}),
      ...(label.trim() ? { label: label.trim() } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Open a count session"
      description="Snapshots the current stock in scope; workers count against it in the worker app."
      submitLabel="Open session"
      pendingLabel="Opening…"
      pending={open.pending}
      {...formDialogError(open.error, open.fieldErrors)}
    >
      <Select
        label="Warehouse"
        value={warehouseId}
        onChange={(e) => setWarehouseId(e.target.value)}
        options={warehouseOptions}
        placeholder={warehousesState.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
        required
        disabled={warehousesState.status !== "success"}
      />
      <RadioGroup
        legend="Scope"
        name="count-scope"
        value={scopeKind}
        onChange={(v) => setScopeKind(v as CountScopeKind)}
        options={SCOPE_OPTIONS}
      />
      {scopeKind === "ZONE" ? (
        <TextField
          label="Zone location id"
          value={zoneLocationId}
          onChange={(e) => setZoneLocationId(e.target.value)}
          placeholder="00000000-0000-0000-0000-000000000000"
          required
        />
      ) : null}
      {scopeKind === "LOCATIONS" ? (
        <TextArea
          label="Location ids"
          value={locationIdsText}
          onChange={(e) => setLocationIdsText(e.target.value)}
          placeholder="One location id per line"
          required
        />
      ) : null}
      <TextField
        label="Label"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="e.g. Q3 stocktake"
      />
      {fieldError ? <p className="text-sm text-danger">{fieldError}</p> : null}
    </FormDialog>
  );
}
