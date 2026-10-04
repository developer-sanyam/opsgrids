import { listBoms } from "@/api/client";
import { Combobox } from "@/ui";
import type { ComboboxOption } from "@/ui";

const BOM_COPY = {
  idle: "Start typing a BOM code or name.",
  noMatches: (q: string) => `No BOM matches "${q}".`,
  noMatchesHint: "Create the BOM first, or plan from the produced item instead.",
  error: "Couldn't search BOMs.",
};

interface BomPickerProps {
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  error?: string | undefined;
}

/** Looks up an active BOM — `GET /operations/manufacturing/boms?q=&is_active=true`. */
export function BomPicker({ value, onChange, error }: BomPickerProps) {
  return (
    <Combobox
      label="BOM"
      value={value}
      onChange={onChange}
      loadOptions={async (q) => {
        const page = await listBoms({ q, is_active: true, size: 20 });
        return page.items.map((b) => ({ id: b.id, label: b.code, sublabel: `${b.name} · ${b.sku}` }));
      }}
      placeholder="Look up a BOM…"
      copy={BOM_COPY}
      error={error}
    />
  );
}
