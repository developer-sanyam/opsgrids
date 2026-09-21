import { useRef } from "react";

import { searchActiveSuppliers } from "@/api/client";
import type { SupplierOut } from "@/api/types";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const SUPPLIER_COPY = {
  idle: "Start typing a supplier's name.",
  noMatches: (q: string) => `No active supplier matches "${q}".`,
  error: "Couldn't load suppliers.",
};

function supplierOption(s: SupplierOut): ComboboxOption {
  return { id: s.id, label: s.name, sublabel: s.code };
}

interface SupplierPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  /** The full supplier record, so the PO form can default currency/category from it. */
  onPick: (supplier: SupplierOut | null) => void;
  error?: string;
}

/**
 * The purchase order form's supplier field — `GET /operations/suppliers?status=active&q=`.
 * Only active suppliers are offered: the server itself refuses a PO against one that isn't.
 */
export function SupplierPicker({ value, onChange, onPick, error }: SupplierPickerProps) {
  const cacheRef = useRef<Map<string, SupplierOut>>(new Map());

  return (
    <Combobox
      label="Supplier"
      value={value}
      onChange={(option) => {
        onChange(option);
        onPick(option ? (cacheRef.current.get(option.id) ?? null) : null);
      }}
      loadOptions={async (q) => {
        const suppliers = await searchActiveSuppliers(q);
        for (const s of suppliers) cacheRef.current.set(s.id, s);
        return suppliers.map(supplierOption);
      }}
      placeholder="Search active suppliers…"
      copy={SUPPLIER_COPY}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
