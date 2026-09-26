import { useRef, useState } from "react";

import { createAsset, updateAsset } from "@/api/client";
import type { Asset, AssetIn, AssetStatus, AssetType, AssetUpdate } from "@/api/types";
import { minorToInput, parseMinor } from "@/lib/money";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";

const TYPE_OPTIONS: { value: AssetType; label: string }[] = [
  { value: "laptop", label: "Laptop" },
  { value: "desktop", label: "Desktop" },
  { value: "server", label: "Server" },
  { value: "monitor", label: "Monitor" },
  { value: "phone", label: "Phone" },
  { value: "network", label: "Network" },
  { value: "license", label: "License" },
];

const STATUS_OPTIONS: { value: AssetStatus; label: string }[] = [
  { value: "in_stock", label: "In stock" },
  { value: "in_use", label: "In use" },
  { value: "in_repair", label: "In repair" },
  { value: "retired", label: "Retired" },
];

interface AssetFormDialogProps {
  /** `null` creates. */
  asset: Asset | null;
  onClose: () => void;
  onSaved: (asset: Asset) => void;
}

/** `POST /itsm/assets` / `PATCH /itsm/assets/{tag}`. `tag` is set once, at create, and is
 *  immutable afterward — it is the API identity and the join key incidents key on. */
export function AssetFormDialog({ asset, onClose, onSaved }: AssetFormDialogProps) {
  const [tag, setTag] = useState(asset?.tag ?? "");
  const [name, setName] = useState(asset?.name ?? "");
  const [type, setType] = useState<AssetType>(asset?.type ?? "laptop");
  const [status, setStatus] = useState<AssetStatus>(asset?.status ?? "in_stock");
  const [owner, setOwner] = useState(asset?.owner ?? "");
  const [location, setLocation] = useState(asset?.location ?? "");
  const [serial, setSerial] = useState(asset?.serial ?? "");
  const [purchasedAt, setPurchasedAt] = useState(asset?.purchased_at ?? "");
  const [warrantyExpiresAt, setWarrantyExpiresAt] = useState(asset?.warranty_expires_at ?? "");
  const [cost, setCost] = useState(asset ? minorToInput(asset.cost_minor, asset.cost_currency) : "");
  const [currency, setCurrency] = useState(asset?.cost_currency ?? "INR");

  const fields = {
    tag,
    name,
    type,
    status,
    owner,
    location,
    serial,
    purchasedAt,
    warrantyExpiresAt,
    cost,
    currency,
  };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [tagError, setTagError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [costError, setCostError] = useState<string | undefined>(undefined);
  const tagRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: AssetIn | AssetUpdate) =>
      asset ? updateAsset(asset.tag, input as AssetUpdate) : createAsset(input as AssetIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setTagError(undefined);
    setNameError(undefined);
    setCostError(undefined);

    if (!asset && !/^[A-Za-z0-9_-]{1,40}$/.test(tag.trim())) {
      setTagError("Letters, numbers, - and _ only, up to 40 characters.");
      tagRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the asset a name.");
      nameRef.current?.focus();
      return;
    }
    const currencyCode = currency.trim().toUpperCase() || "INR";
    const parsedCost = parseMinor(cost, currencyCode);
    if (!parsedCost.ok) {
      setCostError(parsedCost.message);
      return;
    }
    const shared = {
      name: trimmedName,
      type,
      status,
      owner: owner.trim() === "" ? null : owner.trim(),
      location: location.trim(),
      serial: serial.trim(),
      purchased_at: purchasedAt === "" ? null : purchasedAt,
      warranty_expires_at: warrantyExpiresAt === "" ? null : warrantyExpiresAt,
      cost_minor: parsedCost.minor,
      cost_currency: currencyCode,
    };
    if (asset) {
      void save.mutate(shared);
    } else {
      void save.mutate({ tag: tag.trim(), ...shared });
    }
  }

  const hasTopLevelError = save.error && Object.keys(save.fieldErrors).length === 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={asset ? `Edit ${asset.tag}` : "Add an asset"}
      submitLabel={asset ? "Save changes" : "Add asset"}
      pendingLabel={asset ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={asset ? nameRef : tagRef}
      size="lg"
      dirty={dirty}
      error={hasTopLevelError ? problemMessage(save.error) : undefined}
      reference={hasTopLevelError ? (save.error as ApiError).problem?.request_id : undefined}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {asset ? (
          <TextField label="Tag" value={asset.tag} disabled helper="Cannot change once created." />
        ) : (
          <TextField
            ref={tagRef}
            label="Tag"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            maxLength={40}
            placeholder="AST-1099"
            required
            error={tagError ?? save.fieldErrors.tag}
          />
        )}
        <TextField
          ref={nameRef}
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
          required
          error={nameError ?? save.fieldErrors.name}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as AssetType)}
          options={TYPE_OPTIONS}
          error={save.fieldErrors.type}
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as AssetStatus)}
          options={STATUS_OPTIONS}
          error={save.fieldErrors.status}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Owner"
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          optional
          maxLength={120}
          helper="No directory yet — a free-text name."
          error={save.fieldErrors.owner}
        />
        <TextField
          label="Location"
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          optional
          maxLength={160}
          error={save.fieldErrors.location}
        />
      </div>
      <TextField
        label="Serial"
        value={serial}
        onChange={(e) => setSerial(e.target.value)}
        optional
        maxLength={120}
        error={save.fieldErrors.serial}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Purchased"
          type="date"
          value={purchasedAt}
          onChange={(e) => setPurchasedAt(e.target.value)}
          optional
          error={save.fieldErrors.purchased_at}
        />
        <TextField
          label="Warranty expires"
          type="date"
          value={warrantyExpiresAt}
          onChange={(e) => setWarrantyExpiresAt(e.target.value)}
          optional
          error={save.fieldErrors.warranty_expires_at}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Cost"
          inputMode="decimal"
          value={cost}
          onChange={(e) => setCost(e.target.value)}
          optional
          helper="Major units, e.g. 219000 for ₹2,19,000."
          error={costError ?? save.fieldErrors.cost_minor}
        />
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          helper="Three-letter ISO code."
          error={save.fieldErrors.cost_currency}
        />
      </div>
    </FormDialog>
  );
}
