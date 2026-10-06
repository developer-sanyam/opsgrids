import { useRef, useState } from "react";

import { FormDialog, Select, TextArea, TextField } from "@opsgrids/ui";

import { createPlan } from "@/api/catalog";
import type { PlanListItemOut } from "@/api/catalog";
import { KNOWN_PRODUCT_KEYS, productLabel } from "@/lib/labels";
import { ProblemList } from "./ProblemList";
import { useCatalogWrite } from "./useCatalogWrite";

const CODE = /^[a-z0-9][a-z0-9_-]{0,59}$/;

interface CreatePlanDialogProps {
  onClose: () => void;
  onDone: (created: PlanListItemOut) => void;
}

/** A new, version-less plan. The server checks the product against product-service's registry. */
export function CreatePlanDialog({ onClose, onDone }: CreatePlanDialogProps) {
  const [productKey, setProductKey] = useState(KNOWN_PRODUCT_KEYS[0] ?? "");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [sortOrder, setSortOrder] = useState("100");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const nameRef = useRef<HTMLInputElement>(null);
  const write = useCatalogWrite();

  async function handleSubmit() {
    const next: Record<string, string> = {};
    if (!CODE.test(code.trim())) next.code = "Lowercase letters, digits, - and _, up to 60.";
    if (name.trim().length === 0) next.name = "Enter a display name.";
    if (!/^\d{1,4}$/.test(sortOrder.trim())) next.sortOrder = "A whole number, 0 to 9999.";
    if (reason.trim().length < 3) next.reason = "Say why, in at least 3 characters.";
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    const created = await write.run((key) =>
      createPlan(
        {
          product_key: productKey,
          code: code.trim(),
          name: name.trim(),
          sort_order: Number(sortOrder.trim()),
          reason: reason.trim(),
        },
        key,
      ),
    );
    if (created !== undefined) onDone(created);
  }

  const productError = write.fieldErrors.find((e) => e.field === "product_key")?.message;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void handleSubmit()}
      title="New plan"
      description="A plan has no price until you draft and publish a version of it."
      submitLabel="Create plan"
      pendingLabel="Creating plan…"
      pending={write.pending}
      error={write.error}
      reference={write.reference}
      initialFocusRef={nameRef}
      dirty={code !== "" || name !== "" || reason !== ""}
    >
      <Select
        label="Product"
        required
        options={KNOWN_PRODUCT_KEYS.map((key) => ({ value: key, label: `${productLabel(key)} (${key})` }))}
        value={productKey}
        onChange={(event) => setProductKey(event.target.value)}
        error={productError}
      />
      <TextField
        ref={nameRef}
        label="Name"
        required
        maxLength={160}
        value={name}
        onChange={(event) => setName(event.target.value)}
        error={errors.name}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <TextField
          label="Code"
          required
          helper="Stable machine code, unique within the product."
          value={code}
          onChange={(event) => setCode(event.target.value)}
          error={errors.code}
        />
        <TextField
          label="Sort order"
          required
          inputMode="numeric"
          value={sortOrder}
          onChange={(event) => setSortOrder(event.target.value)}
          error={errors.sortOrder}
        />
      </div>
      <TextArea
        label="Reason"
        required
        rows={2}
        maxLength={500}
        helper="Kept on the audit record with your name."
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        error={errors.reason}
      />
      <ProblemList problems={write.fieldErrors.filter((e) => e.field !== "product_key")} />
    </FormDialog>
  );
}
