import { useRef, useState } from "react";

import { createCatalogItem, updateCatalogItem } from "@/api/client";
import type {
  CatalogCategory,
  CatalogItem,
  CatalogItemIn,
  CatalogItemUpdate,
} from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField } from "@opsgrids/ui";

const CATEGORY_OPTIONS: { value: CatalogCategory; label: string }[] = [
  { value: "onboarding", label: "Onboarding" },
  { value: "offboarding", label: "Offboarding" },
  { value: "access", label: "Access" },
  { value: "hardware", label: "Hardware" },
  { value: "software", label: "Software" },
];

interface CatalogItemFormDialogProps {
  /** `null` creates. */
  item: CatalogItem | null;
  onClose: () => void;
  onSaved: (item: CatalogItem) => void;
}

/** `POST /itsm/catalog` / `PATCH /itsm/catalog/{code}`. `id` (the code) is set once, at create,
 *  and never appears in the edit payload — it is the join key `requests.catalog_item_code`. */
export function CatalogItemFormDialog({ item, onClose, onSaved }: CatalogItemFormDialogProps) {
  const [code, setCode] = useState(item?.id ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [category, setCategory] = useState<CatalogCategory>(item?.category ?? "software");
  const [description, setDescription] = useState(item?.description ?? "");
  const [slaDays, setSlaDays] = useState(item ? String(item.sla_days) : "1");

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [slaError, setSlaError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: CatalogItemIn | CatalogItemUpdate) =>
      item ? updateCatalogItem(item.id, input as CatalogItemUpdate) : createCatalogItem(input as CatalogItemIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);
    setSlaError(undefined);

    if (!item) {
      const trimmedCode = code.trim();
      if (!/^[A-Za-z0-9_-]{1,20}$/.test(trimmedCode)) {
        setCodeError("Letters, numbers, - and _ only, up to 20 characters.");
        codeRef.current?.focus();
        return;
      }
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the item a name.");
      nameRef.current?.focus();
      return;
    }
    const sla = Number(slaDays);
    if (!Number.isInteger(sla) || sla < 1) {
      setSlaError("A whole number of days, at least 1.");
      return;
    }

    if (item) {
      void save.mutate({
        name: trimmedName,
        category,
        description: description.trim(),
        sla_days: sla,
      });
    } else {
      void save.mutate({
        id: code.trim(),
        name: trimmedName,
        category,
        description: description.trim(),
        sla_days: sla,
      });
    }
  }

  const hasTopLevelError = save.error && Object.keys(save.fieldErrors).length === 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={item ? `Edit ${item.name}` : "Add a catalog item"}
      submitLabel={item ? "Save changes" : "Add item"}
      pendingLabel={item ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={item ? nameRef : codeRef}
      error={hasTopLevelError ? problemMessage(save.error) : undefined}
      reference={hasTopLevelError ? (save.error as ApiError).problem?.request_id : undefined}
    >
      {item ? null : (
        <TextField
          ref={codeRef}
          label="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          maxLength={20}
          helper="The catalog item's permanent identity, e.g. CAT-11. Cannot change later."
          error={codeError ?? save.fieldErrors.id}
        />
      )}
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={160}
        error={nameError ?? save.fieldErrors.name}
      />
      <Select
        label="Category"
        value={category}
        onChange={(e) => setCategory(e.target.value as CatalogCategory)}
        options={CATEGORY_OPTIONS}
        error={save.fieldErrors.category}
      />
      <TextArea
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        optional
        maxLength={2000}
        error={save.fieldErrors.description}
      />
      <TextField
        label="SLA (working days)"
        inputMode="numeric"
        value={slaDays}
        onChange={(e) => setSlaDays(e.target.value)}
        error={slaError ?? save.fieldErrors.sla_days}
      />
    </FormDialog>
  );
}
