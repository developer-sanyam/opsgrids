/**
 * The product form's own values, validation and mapping to `ProductInput` — kept out of the
 * component so the rules (especially the variant/price-band invariant) are testable with no DOM.
 */

import { minorToInput, parseOptionalMinor, parseRequiredMinor } from "./money";
import type { CategoryOut, ProductInput, ProductOut, ProductStatus, ProductVariantInput } from "./types";

export interface VariantFormValues {
  /** A client-only row key, stable across reorders — never sent. */
  rowId: string;
  id?: string;
  label: string;
  priceMin: string;
  priceMax: string;
  mrp: string;
  inStock: boolean;
}

export interface ProductFormValues {
  code: string;
  name: string;
  brand: string;
  categoryId: string;
  spec: string;
  unit: string;
  description: string;
  priceMin: string;
  priceMax: string;
  mrp: string;
  gstInclusive: boolean;
  moq: string;
  moqUnit: string;
  inStock: boolean;
  leadTimeDays: string;
  deliveryEtaMinutes: string;
  featured: boolean;
  imageUrl: string;
  status: ProductStatus;
  isActive: boolean;
  variants: VariantFormValues[];
}

let rowSeq = 0;
export function newVariantRow(): VariantFormValues {
  rowSeq += 1;
  return { rowId: `row-${rowSeq}`, label: "", priceMin: "", priceMax: "", mrp: "", inStock: true };
}

export function emptyProductForm(defaultCategoryId: string): ProductFormValues {
  return {
    code: "",
    name: "",
    brand: "",
    categoryId: defaultCategoryId,
    spec: "",
    unit: "",
    description: "",
    priceMin: "",
    priceMax: "",
    mrp: "",
    gstInclusive: true,
    moq: "1",
    moqUnit: "",
    inStock: true,
    leadTimeDays: "0",
    deliveryEtaMinutes: "",
    featured: false,
    imageUrl: "",
    status: "draft",
    isActive: true,
    variants: [],
  };
}

export function fromProduct(product: ProductOut): ProductFormValues {
  return {
    code: product.code,
    name: product.name,
    brand: product.brand,
    categoryId: product.category_id,
    spec: product.spec,
    unit: product.unit,
    description: product.description ?? "",
    priceMin: minorToInput(product.price_min_minor),
    priceMax: minorToInput(product.price_max_minor),
    mrp: minorToInput(product.mrp_minor),
    gstInclusive: product.gst_inclusive,
    moq: String(product.moq),
    moqUnit: product.moq_unit,
    inStock: product.in_stock,
    leadTimeDays: String(product.lead_time_days),
    deliveryEtaMinutes: product.delivery_eta_minutes === null ? "" : String(product.delivery_eta_minutes),
    featured: product.featured,
    imageUrl: product.image_url ?? "",
    status: product.status,
    isActive: product.is_active,
    variants: product.variants.map((v) => ({
      rowId: v.id,
      id: v.id,
      label: v.label,
      priceMin: minorToInput(v.price_min_minor),
      priceMax: minorToInput(v.price_max_minor),
      mrp: minorToInput(v.mrp_minor),
      inStock: v.in_stock,
    })),
  };
}

export type VariantRowErrors = Partial<Record<"label" | "priceMin" | "priceMax" | "mrp", string>>;

export type ProductFormErrors = Partial<
  Record<
    | "code"
    | "name"
    | "categoryId"
    | "priceMin"
    | "priceMax"
    | "mrp"
    | "moq"
    | "leadTimeDays"
    | "deliveryEtaMinutes"
    | "imageUrl",
    string
  >
> & { variants?: Record<string, VariantRowErrors> };

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

/** Mirrors the server's rules and never replaces them — a 422 the server still sends lands on
 *  the same fields through `useMutation`'s `fieldErrors`. */
export function validateProductForm(values: ProductFormValues, categories: CategoryOut[]): ProductFormErrors {
  const errors: ProductFormErrors = {};
  if (values.code.trim() === "") errors.code = "Enter a product code.";
  if (values.name.trim() === "") errors.name = "Enter a name.";
  if (!categories.some((c) => c.id === values.categoryId)) errors.categoryId = "Pick a category.";

  const hasVariants = values.variants.length > 0;
  if (!hasVariants) {
    const min = parseRequiredMinor(values.priceMin, "the minimum price");
    if (!min.ok) errors.priceMin = min.message;
    const max = parseRequiredMinor(values.priceMax, "the maximum price");
    if (!max.ok) errors.priceMax = max.message;
    if (min.ok && max.ok && min.minor > max.minor) {
      errors.priceMax = "The maximum price must be at or above the minimum.";
    }
    const mrp = parseOptionalMinor(values.mrp);
    if (!mrp.ok) errors.mrp = mrp.message;
    else if (mrp.minor !== null && min.ok && mrp.minor < min.minor) {
      errors.mrp = "The MRP can't be below the selling price.";
    }
  }

  if (!/^\d+$/.test(values.moq.trim()) || Number(values.moq) < 1) {
    errors.moq = "Enter a whole number of at least 1.";
  }
  if (!/^\d+$/.test(values.leadTimeDays.trim())) {
    errors.leadTimeDays = "Enter a whole number of days, 0 or more.";
  }
  if (values.deliveryEtaMinutes.trim() !== "" && !/^\d+$/.test(values.deliveryEtaMinutes.trim())) {
    errors.deliveryEtaMinutes = "Enter a whole number of minutes, or leave it blank.";
  }
  if (values.imageUrl.trim() !== "" && !isHttpsUrl(values.imageUrl.trim())) {
    errors.imageUrl = "Enter a full https:// image address.";
  }

  const variantErrors: Record<string, Partial<Record<"label" | "priceMin" | "priceMax" | "mrp", string>>> = {};
  for (const variant of values.variants) {
    const rowErrors: Partial<Record<"label" | "priceMin" | "priceMax" | "mrp", string>> = {};
    if (variant.label.trim() === "") rowErrors.label = "Name this pack.";
    const min = parseRequiredMinor(variant.priceMin, "the minimum price");
    if (!min.ok) rowErrors.priceMin = min.message;
    const max = parseRequiredMinor(variant.priceMax, "the maximum price");
    if (!max.ok) rowErrors.priceMax = max.message;
    if (min.ok && max.ok && min.minor > max.minor) rowErrors.priceMax = "Must be at or above the minimum.";
    const mrp = parseOptionalMinor(variant.mrp);
    if (!mrp.ok) rowErrors.mrp = mrp.message;
    else if (mrp.minor !== null && min.ok && mrp.minor < min.minor) rowErrors.mrp = "Below the selling price.";
    if (Object.keys(rowErrors).length > 0) variantErrors[variant.rowId] = rowErrors;
  }
  if (Object.keys(variantErrors).length > 0) errors.variants = variantErrors;

  return errors;
}

function toVariantInput(variant: VariantFormValues): ProductVariantInput {
  const min = parseRequiredMinor(variant.priceMin, "price");
  const max = parseRequiredMinor(variant.priceMax, "price");
  const mrp = parseOptionalMinor(variant.mrp);
  return {
    ...(variant.id ? { id: variant.id } : {}),
    label: variant.label.trim(),
    price_min_minor: min.ok ? min.minor : 0,
    price_max_minor: max.ok ? max.minor : 0,
    mrp_minor: mrp.ok ? mrp.minor : null,
    in_stock: variant.inStock,
  };
}

/** Assumes `validateProductForm` already passed. When variants exist, the product-level band is
 *  `variants[0]`'s — the same invariant the server enforces, mirrored here so the form never
 *  sends a mismatch the server would refuse. */
export function toProductInput(values: ProductFormValues): ProductInput {
  const variants = values.variants.map(toVariantInput);
  const first = variants[0];
  const min = parseRequiredMinor(values.priceMin, "price");
  const max = parseRequiredMinor(values.priceMax, "price");
  const mrp = parseOptionalMinor(values.mrp);
  return {
    code: values.code.trim(),
    name: values.name.trim(),
    brand: values.brand.trim(),
    category_id: values.categoryId,
    spec: values.spec.trim(),
    unit: values.unit.trim(),
    description: values.description.trim() === "" ? null : values.description.trim(),
    price_min_minor: first ? first.price_min_minor : min.ok ? min.minor : 0,
    price_max_minor: first ? first.price_max_minor : max.ok ? max.minor : 0,
    mrp_minor: first ? first.mrp_minor : mrp.ok ? mrp.minor : null,
    gst_inclusive: values.gstInclusive,
    moq: Number(values.moq),
    moq_unit: values.moqUnit.trim(),
    in_stock: values.inStock,
    lead_time_days: Number(values.leadTimeDays),
    delivery_eta_minutes: values.deliveryEtaMinutes.trim() === "" ? null : Number(values.deliveryEtaMinutes),
    featured: values.featured,
    image_url: values.imageUrl.trim() === "" ? null : values.imageUrl.trim(),
    status: values.status,
    is_active: values.isActive,
    variants,
  };
}
