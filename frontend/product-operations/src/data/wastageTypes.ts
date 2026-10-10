import type { WastageType } from "@/api/types";
import { MANUAL_WASTAGE_TYPES } from "@/lib/productionOrderActions";
import type { SelectOption } from "@/ui";

/** The one label map for a wastage type, so "PROCESS_LOSS" reads "Process loss" everywhere. */
export const WASTAGE_TYPE_LABEL: Record<WastageType, string> = {
  CUTTING: "Cutting",
  DEFECT: "Defect",
  SPILLAGE: "Spillage",
  DAMAGE: "Damage",
  PROCESS_LOSS: "Process loss",
  LEFTOVER: "Leftover",
  OTHER: "Other",
};

/** What a person can record by hand — never LEFTOVER, which only completing an order writes. */
export const MANUAL_WASTAGE_OPTIONS: SelectOption[] = MANUAL_WASTAGE_TYPES.map((t) => ({
  value: t,
  label: WASTAGE_TYPE_LABEL[t],
}));

/** Every type, for a threshold, where "leftover" is a legitimate thing to cap. */
export const ALL_WASTAGE_OPTIONS: SelectOption[] = (Object.keys(WASTAGE_TYPE_LABEL) as WastageType[]).map((t) => ({
  value: t,
  label: WASTAGE_TYPE_LABEL[t],
}));
