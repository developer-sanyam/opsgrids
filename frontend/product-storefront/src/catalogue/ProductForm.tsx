import { useRef, useState } from "react";

import { problemMessage, useMutation } from "@opsgrids/client";
import type { ApiError } from "@opsgrids/client";
import { Button, FormDialog, InlineAlert, Select, Switch, TextField } from "@opsgrids/ui";

import { createProduct, updateProduct } from "./api";
import { isHttpsImageUrl } from "./imageUrl";
import {
  emptyProductForm,
  fromProduct,
  newVariantRow,
  toProductInput,
  validateProductForm,
} from "./productFormValues";
import type {
  ProductFormErrors,
  ProductFormValues,
  VariantFormValues,
  VariantRowErrors,
} from "./productFormValues";
import type { CategoryOut, ProductOut, ProductStatus } from "./types";

const STATUS_OPTIONS: { value: ProductStatus; label: string }[] = [
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

function VariantRow({
  variant,
  errors,
  disabled,
  onChange,
  onRemove,
}: {
  variant: VariantFormValues;
  errors: VariantRowErrors | undefined;
  disabled: boolean;
  onChange: (next: VariantFormValues) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line p-3">
      <div className="flex items-start justify-between gap-3">
        <TextField
          label="Pack label"
          value={variant.label}
          onChange={(e) => onChange({ ...variant, label: e.target.value })}
          placeholder="1 kg"
          disabled={disabled}
          containerClassName="flex-1"
          {...(errors?.label !== undefined ? { error: errors.label } : {})}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRemove}
          disabled={disabled}
          className="mt-6"
        >
          Remove
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label="Min price"
          inputMode="decimal"
          value={variant.priceMin}
          onChange={(e) => onChange({ ...variant, priceMin: e.target.value })}
          disabled={disabled}
          {...(errors?.priceMin !== undefined ? { error: errors.priceMin } : {})}
        />
        <TextField
          label="Max price"
          inputMode="decimal"
          value={variant.priceMax}
          onChange={(e) => onChange({ ...variant, priceMax: e.target.value })}
          disabled={disabled}
          {...(errors?.priceMax !== undefined ? { error: errors.priceMax } : {})}
        />
        <TextField
          label="MRP"
          inputMode="decimal"
          value={variant.mrp}
          onChange={(e) => onChange({ ...variant, mrp: e.target.value })}
          optional
          disabled={disabled}
          {...(errors?.mrp !== undefined ? { error: errors.mrp } : {})}
        />
      </div>
      <Switch
        label="In stock"
        checked={variant.inStock}
        onChange={(checked) => onChange({ ...variant, inStock: checked })}
        disabled={disabled}
      />
    </div>
  );
}

export function ProductForm({
  product,
  categories,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  product: ProductOut | null;
  categories: CategoryOut[];
  onClose: () => void;
  onSaved: (product: ProductOut) => void;
}) {
  const [values, setValues] = useState<ProductFormValues>(() =>
    product ? fromProduct(product) : emptyProductForm(categories[0]?.id ?? ""),
  );
  const [errors, setErrors] = useState<ProductFormErrors>({});
  const codeRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: ReturnType<typeof toProductInput>) =>
      product ? updateProduct(product.id, input) : createProduct(input),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  function set<K extends keyof ProductFormValues>(key: K, value: ProductFormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function submit() {
    const found = validateProductForm(values, categories);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      if (found.code) codeRef.current?.focus();
      else if (found.name) nameRef.current?.focus();
      return;
    }
    void save.mutate(toProductInput(values));
  }

  const hasVariants = values.variants.length > 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={product ? `Edit ${product.name}` : "New product"}
      submitLabel={product ? "Save changes" : "Create product"}
      pendingLabel={product ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={product ? nameRef : codeRef}
      size="lg"
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? { error: problemMessage(save.error), reference: (save.error as ApiError).problem?.request_id }
        : {})}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          ref={codeRef}
          label="Code"
          value={values.code}
          onChange={(e) => set("code", e.target.value)}
          helper={product ? "Set once, when the product is created." : "What a buyer quotes on a PO."}
          disabled={product !== null}
          error={errors.code ?? save.fieldErrors.code}
        />
        <TextField
          ref={nameRef}
          label="Name"
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
          error={errors.name ?? save.fieldErrors.name}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Brand"
          value={values.brand}
          onChange={(e) => set("brand", e.target.value)}
          optional
          error={save.fieldErrors.brand}
        />
        <Select
          label="Category"
          value={values.categoryId}
          options={categories.map((c) => ({ value: c.id, label: c.label }))}
          onChange={(e) => set("categoryId", e.target.value)}
          error={errors.categoryId ?? save.fieldErrors.category_id}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Specification"
          value={values.spec}
          onChange={(e) => set("spec", e.target.value)}
          optional
        />
        <TextField
          label="Selling unit"
          value={values.unit}
          onChange={(e) => set("unit", e.target.value)}
          placeholder="per 100 pcs"
          optional
        />
      </div>

      {hasVariants ? (
        <InlineAlert tone="info" live="off" title="Priced from its packs">
          This product has pack sizes below — its price band and MRP come from the first pack,
          "{values.variants[0]?.label || "the first row"}". Reorder the rows to change which pack
          leads.
        </InlineAlert>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <TextField
            label="Min price"
            inputMode="decimal"
            value={values.priceMin}
            onChange={(e) => set("priceMin", e.target.value)}
            error={errors.priceMin ?? save.fieldErrors.price_min_minor}
          />
          <TextField
            label="Max price"
            inputMode="decimal"
            value={values.priceMax}
            onChange={(e) => set("priceMax", e.target.value)}
            helper="Equal to the min for a single published price."
            error={errors.priceMax ?? save.fieldErrors.price_max_minor}
          />
          <TextField
            label="MRP"
            inputMode="decimal"
            value={values.mrp}
            onChange={(e) => set("mrp", e.target.value)}
            optional
            error={errors.mrp ?? save.fieldErrors.mrp_minor}
          />
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-ink">Pack sizes</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => set("variants", [...values.variants, newVariantRow()])}
          >
            Add a pack
          </Button>
        </div>
        {values.variants.length === 0 ? (
          <p className="text-xs text-muted">No pack sizes — this is a single-pack line.</p>
        ) : (
          values.variants.map((variant) => (
            <VariantRow
              key={variant.rowId}
              variant={variant}
              errors={errors.variants?.[variant.rowId]}
              disabled={save.pending}
              onChange={(next) =>
                set(
                  "variants",
                  values.variants.map((v) => (v.rowId === variant.rowId ? next : v)),
                )
              }
              onRemove={() =>
                set("variants", values.variants.filter((v) => v.rowId !== variant.rowId))
              }
            />
          ))
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label="MOQ"
          inputMode="numeric"
          value={values.moq}
          onChange={(e) => set("moq", e.target.value)}
          error={errors.moq ?? save.fieldErrors.moq}
        />
        <TextField
          label="MOQ unit"
          value={values.moqUnit}
          onChange={(e) => set("moqUnit", e.target.value)}
          placeholder="pcs"
          optional
        />
        <TextField
          label="Lead time (days)"
          inputMode="numeric"
          value={values.leadTimeDays}
          onChange={(e) => set("leadTimeDays", e.target.value)}
          error={errors.leadTimeDays ?? save.fieldErrors.lead_time_days}
        />
      </div>

      <TextField
        label="Delivery ETA (minutes)"
        inputMode="numeric"
        value={values.deliveryEtaMinutes}
        onChange={(e) => set("deliveryEtaMinutes", e.target.value)}
        optional
        helper="The quick-commerce promise. Leave blank for a line with no delivery-time claim."
        error={errors.deliveryEtaMinutes ?? save.fieldErrors.delivery_eta_minutes}
      />

      <TextField
        label="Image address"
        value={values.imageUrl}
        onChange={(e) => set("imageUrl", e.target.value)}
        placeholder="https://"
        optional
        helper="A full https:// address, with a preview below. Uploads aren't available on this box yet."
        error={errors.imageUrl ?? save.fieldErrors.image_url}
      />
      {values.imageUrl.trim() !== "" && isHttpsImageUrl(values.imageUrl.trim()) ? (
        <div className="h-24 w-full overflow-hidden rounded-tile border border-line bg-ground">
          <img src={values.imageUrl.trim()} alt="" className="h-full w-full object-cover" />
        </div>
      ) : null}

      <Select
        label="Status"
        value={values.status}
        options={STATUS_OPTIONS}
        onChange={(e) => set("status", e.target.value as ProductStatus)}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <Switch
          label="GST inclusive"
          checked={values.gstInclusive}
          onChange={(checked) => set("gstInclusive", checked)}
        />
        <Switch label="In stock" checked={values.inStock} onChange={(checked) => set("inStock", checked)} />
        <Switch label="Featured" checked={values.featured} onChange={(checked) => set("featured", checked)} />
        <Switch
          label="Active"
          checked={values.isActive}
          onChange={(checked) => set("isActive", checked)}
          description="Inactive products are hidden from buyers."
        />
      </div>
    </FormDialog>
  );
}
