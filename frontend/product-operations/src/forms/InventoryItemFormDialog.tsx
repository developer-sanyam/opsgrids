import { useRef, useState } from "react";

import { createInventoryItem, updateInventoryItem } from "@/api/client";
import type { Category, InventoryItemIn, InventoryItemOut, InventoryItemPatch } from "@/api/types";
import { useItemCategoryOptions } from "@/data/useItemCategoryOptions";
import { useUomOptions } from "@/data/useUomOptions";
import { useWarehouseOptions } from "@/data/useWarehouseOptions";
import { minorToInput, parseInt0, parseMinor } from "@/lib/money";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import {
  CheckboxGroup,
  FormDialog,
  FormSection,
  Select,
  Stepper,
  Switch,
  TextField,
} from "@/ui";
import type { StepItem } from "@/ui";

const TRACKING_OPTIONS = [
  { value: "track_lot", label: "Lot tracking" },
  { value: "track_roll", label: "Roll tracking" },
  { value: "track_expiry", label: "Expiry tracking" },
  { value: "track_quality", label: "Quality status tracking" },
] as const;

const CATEGORY_OPTIONS: { value: Category; label: string }[] = [
  { value: "raw_materials", label: "Raw materials" },
  { value: "packaging", label: "Packaging" },
  { value: "components", label: "Components" },
  { value: "mro", label: "MRO" },
  { value: "logistics", label: "Logistics" },
  { value: "services", label: "Services" },
];

const SKU_PATTERN = /^[A-Za-z0-9._-]+$/;

const STEPS: StepItem[] = [
  { id: "details", title: "Item details", description: "Identity, stock and value" },
  { id: "master", title: "Item master", description: "Optional classification and tracking" },
];

interface InventoryItemFormDialogProps {
  /** `null` creates. */
  item: InventoryItemOut | null;
  onClose: () => void;
  onSaved: (item: InventoryItemOut) => void;
}

/**
 * Create or edit an inventory item's card — `POST /operations/inventory` /
 * `PATCH /operations/inventory/{id}`. `sku`, `currency` and `on_hand` are never editable here:
 * the SKU is the join key a PO line uses, the currency is the unit the value accumulated in, and
 * stock only ever moves through "Adjust stock".
 *
 * Two steps behind the shared `Stepper`: item details (what a PO line needs), then the optional
 * item master fields. `FormDialog`'s own footer doubles as each step's forward action — "Continue"
 * on the first step, the real save on the second — so there is exactly one primary button on
 * screen at a time; the way back to the first step is the step rail itself.
 */
export function InventoryItemFormDialog({ item, onClose, onSaved }: InventoryItemFormDialogProps) {
  const warehouses = useWarehouseOptions();
  const itemCategories = useItemCategoryOptions();
  const secondaryUoms = useUomOptions();
  const [step, setStep] = useState(0);
  const [sku, setSku] = useState(item?.sku ?? "");
  const [name, setName] = useState(item?.name ?? "");
  const [category, setCategory] = useState<Category | "">(item?.category ?? "");
  const [warehouse, setWarehouse] = useState(item?.warehouse ?? "");
  const [uom, setUom] = useState(item?.uom ?? "");
  const [reorderPoint, setReorderPoint] = useState(String(item?.reorder_point ?? 0));
  const [unitValue, setUnitValue] = useState(item ? minorToInput(item.unit_value_minor) : "");
  const [currency, setCurrency] = useState(item?.currency ?? "INR");
  const [openingOnHand, setOpeningOnHand] = useState("0");
  const [itemCategoryId, setItemCategoryId] = useState(item?.item_category_id ?? "");
  const [secondaryUomId, setSecondaryUomId] = useState(item?.secondary_uom_id ?? "");
  const [hsnCode, setHsnCode] = useState(item?.hsn_code ?? "");
  const [tracking, setTracking] = useState<string[]>(
    TRACKING_OPTIONS.filter((o) => item?.[o.value]).map((o) => o.value),
  );
  const [isActive, setIsActive] = useState(item?.is_active ?? true);

  const [skuError, setSkuError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [categoryError, setCategoryError] = useState<string | undefined>(undefined);
  const [warehouseError, setWarehouseError] = useState<string | undefined>(undefined);
  const [uomError, setUomError] = useState<string | undefined>(undefined);
  const [reorderError, setReorderError] = useState<string | undefined>(undefined);
  const [valueError, setValueError] = useState<string | undefined>(undefined);
  const [openingError, setOpeningError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: InventoryItemIn | InventoryItemPatch) =>
      item
        ? updateInventoryItem(item.id, input as InventoryItemPatch)
        : createInventoryItem(input as InventoryItemIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  const dirty =
    sku !== (item?.sku ?? "") ||
    name !== (item?.name ?? "") ||
    category !== (item?.category ?? "") ||
    warehouse !== (item?.warehouse ?? "") ||
    uom !== (item?.uom ?? "") ||
    reorderPoint !== String(item?.reorder_point ?? 0) ||
    unitValue !== (item ? minorToInput(item.unit_value_minor) : "") ||
    (!item && (currency !== "INR" || openingOnHand !== "0")) ||
    itemCategoryId !== (item?.item_category_id ?? "") ||
    secondaryUomId !== (item?.secondary_uom_id ?? "") ||
    hsnCode !== (item?.hsn_code ?? "") ||
    tracking.join(",") !== TRACKING_OPTIONS.filter((o) => item?.[o.value]).map((o) => o.value).join(",") ||
    isActive !== (item?.is_active ?? true);

  const detailsInvalid = Boolean(
    skuError ??
      nameError ??
      categoryError ??
      warehouseError ??
      uomError ??
      reorderError ??
      valueError ??
      openingError ??
      save.fieldErrors.sku ??
      save.fieldErrors.name ??
      save.fieldErrors.category ??
      save.fieldErrors.warehouse ??
      save.fieldErrors.uom ??
      save.fieldErrors.reorder_point ??
      save.fieldErrors.unit_value_minor ??
      save.fieldErrors.currency,
  );
  const stepItems: StepItem[] = STEPS.map((s, index) => ({
    ...s,
    invalid: index === 0 ? detailsInvalid : false,
  }));

  /** Everything the details step is responsible for. Returns whether it's clean. */
  function validateDetails(): boolean {
    setSkuError(undefined);
    setNameError(undefined);
    setCategoryError(undefined);
    setWarehouseError(undefined);
    setUomError(undefined);
    setReorderError(undefined);
    setValueError(undefined);
    setOpeningError(undefined);

    const trimmedSku = sku.trim();
    if (!item && (trimmedSku.length < 1 || !SKU_PATTERN.test(trimmedSku))) {
      setSkuError("Letters, numbers, dots, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return false;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the item a name.");
      return false;
    }
    if (category === "") {
      setCategoryError("Choose a category.");
      return false;
    }
    if (warehouse === "") {
      setWarehouseError("Choose a warehouse.");
      return false;
    }
    const trimmedUom = uom.trim();
    if (trimmedUom.length < 1) {
      setUomError("Give it a unit of measure, like MT, pcs or L.");
      return false;
    }
    const reorder = parseInt0(reorderPoint);
    if (!reorder.ok) {
      setReorderError(reorder.message);
      return false;
    }
    const value = parseMinor(unitValue);
    if (!value.ok) {
      setValueError(value.message);
      return false;
    }
    if (!item) {
      const opening = parseInt0(openingOnHand);
      if (!opening.ok) {
        setOpeningError(opening.message);
        return false;
      }
    }
    return true;
  }

  function continueToMaster() {
    if (validateDetails()) setStep(1);
  }

  function submit() {
    if (!validateDetails()) {
      setStep(0);
      return;
    }
    // `validateDetails` above already refused an empty category — this is only for the
    // narrowing, so the payload below can carry a `Category` rather than `Category | ""`.
    if (category === "") return;

    const trimmedName = name.trim();
    const trimmedUom = uom.trim();
    const reorder = parseInt0(reorderPoint);
    const value = parseMinor(unitValue);
    const trackingFlags = {
      track_lot: tracking.includes("track_lot"),
      track_roll: tracking.includes("track_roll"),
      track_expiry: tracking.includes("track_expiry"),
      track_quality: tracking.includes("track_quality"),
    };

    if (item) {
      const patch: InventoryItemPatch = {
        name: trimmedName,
        category,
        warehouse,
        uom: trimmedUom,
        reorder_point: reorder.ok ? reorder.value : 0,
        unit_value_minor: value.ok ? value.minor : 0,
        item_category_id: itemCategoryId === "" ? null : itemCategoryId,
        secondary_uom_id: secondaryUomId === "" ? null : secondaryUomId,
        hsn_code: hsnCode.trim(),
        ...trackingFlags,
        is_active: isActive,
      };
      void save.mutate(patch);
      return;
    }

    const opening = parseInt0(openingOnHand);
    const input: InventoryItemIn = {
      sku: sku.trim(),
      name: trimmedName,
      category,
      warehouse,
      uom: trimmedUom,
      reorder_point: reorder.ok ? reorder.value : 0,
      unit_value_minor: value.ok ? value.minor : 0,
      currency: currency.trim().toUpperCase() || "INR",
      opening_on_hand: opening.ok ? opening.value : 0,
      ...(itemCategoryId === "" ? {} : { item_category_id: itemCategoryId }),
      ...(secondaryUomId === "" ? {} : { secondary_uom_id: secondaryUomId }),
      hsn_code: hsnCode.trim(),
      ...trackingFlags,
      is_active: isActive,
    };
    void save.mutate(input);
  }

  function handleSubmit() {
    if (step === 0) continueToMaster();
    else submit();
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={handleSubmit}
      title={item ? `Edit ${item.name}` : "Add inventory item"}
      submitLabel={step === 0 ? "Continue" : item ? "Save changes" : "Create item"}
      pendingLabel={item ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <Stepper steps={stepItems} current={step} onStepChange={setStep} label="Item form progress" />

      {step === 0 ? (
        <FormSection title="Item details" headingLevel={3}>
          <TextField
            ref={firstFieldRef}
            label="SKU"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            disabled={item !== null}
            helper={item ? "A SKU can't change once created." : "Letters, numbers, . - and _."}
            required={item === null}
            {...((skuError ?? save.fieldErrors.sku) !== undefined ? { error: skuError ?? save.fieldErrors.sku } : {})}
          />
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
          />
          <Select
            label="Category"
            value={category}
            onChange={(e) => setCategory(e.target.value as Category)}
            placeholder="Choose a category"
            options={CATEGORY_OPTIONS}
            required
            {...((categoryError ?? save.fieldErrors.category) !== undefined ? { error: categoryError ?? save.fieldErrors.category } : {})}
          />
          <Select
            label="Warehouse"
            value={warehouse}
            onChange={(e) => setWarehouse(e.target.value)}
            placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
            options={warehouses.options}
            disabled={warehouses.status !== "success" || warehouses.options.length === 0}
            required
            {...(warehouses.status === "success" && warehouses.options.length === 0
              ? { helper: "Add a warehouse first, on the Warehouses screen." }
              : {})}
            {...((warehouseError ?? save.fieldErrors.warehouse) !== undefined ? { error: warehouseError ?? save.fieldErrors.warehouse } : {})}
          />
          <TextField
            label="Unit of measure"
            value={uom}
            onChange={(e) => setUom(e.target.value)}
            placeholder="MT, pcs, L…"
            required
            {...((uomError ?? save.fieldErrors.uom) !== undefined ? { error: uomError ?? save.fieldErrors.uom } : {})}
          />
          <TextField
            label="Reorder point"
            inputMode="numeric"
            value={reorderPoint}
            onChange={(e) => setReorderPoint(e.target.value)}
            required
            {...((reorderError ?? save.fieldErrors.reorder_point) !== undefined ? { error: reorderError ?? save.fieldErrors.reorder_point } : {})}
          />
          <TextField
            label="Unit value"
            inputMode="decimal"
            value={unitValue}
            onChange={(e) => setUnitValue(e.target.value)}
            helper="Major units per unit of measure."
            required
            {...((valueError ?? save.fieldErrors.unit_value_minor) !== undefined ? { error: valueError ?? save.fieldErrors.unit_value_minor } : {})}
          />

          {item ? null : (
            <>
              <TextField
                label="Opening quantity"
                inputMode="numeric"
                value={openingOnHand}
                onChange={(e) => setOpeningOnHand(e.target.value)}
                helper="Recorded as this item's first stock movement."
                {...((openingError) !== undefined ? { error: openingError } : {})}
              />
              <TextField
                label="Currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                maxLength={3}
                helper="Three-letter ISO code."
                required
                {...((save.fieldErrors.currency) !== undefined ? { error: save.fieldErrors.currency } : {})}
              />
            </>
          )}
        </FormSection>
      ) : (
        <FormSection
          title="Item master"
          description="Optional classification and tracking — all additive, all editable later."
          headingLevel={3}
        >
          <Select
            label="Item category"
            value={itemCategoryId}
            onChange={(e) => setItemCategoryId(e.target.value)}
            placeholder={
              itemCategories.status === "loading" ? "Loading categories…" : "No category"
            }
            options={itemCategories.options}
            disabled={itemCategories.status !== "success"}
            helper="The org's own catalogue tree — separate from the procurement category above."
          />
          <Select
            label="Secondary UOM"
            value={secondaryUomId}
            onChange={(e) => setSecondaryUomId(e.target.value)}
            placeholder={secondaryUoms.status === "loading" ? "Loading units…" : "None"}
            options={secondaryUoms.options}
            disabled={secondaryUoms.status !== "success"}
            helper="An alternate reporting or purchase unit."
          />
          <TextField
            label="HSN code"
            value={hsnCode}
            onChange={(e) => setHsnCode(e.target.value)}
            placeholder="e.g. 5208"
            helper="Tax classification, optional."
          />
          <div className="col-span-full grid gap-4 sm:grid-cols-2">
            <CheckboxGroup
              label="Tracking"
              options={TRACKING_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              value={tracking}
              onChange={setTracking}
              orientation="horizontal"
              helper="What this item's stock keeps a record of, beyond its ledger bucket."
            />
            <Switch
              checked={isActive}
              onChange={setIsActive}
              label="Active"
              description={
                isActive
                  ? "Shows up in item pickers and new transactions."
                  : "Hidden from pickers; its history is untouched."
              }
            />
          </div>
        </FormSection>
      )}
    </FormDialog>
  );
}
