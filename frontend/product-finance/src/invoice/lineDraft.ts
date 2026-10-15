/** One line as typed. Strings, because they are what the inputs hold; parsed on review. */
export interface LineDraft {
  /** Stable identity for React keys and error lookup; an index moves when a line above is removed. */
  id: string;
  description: string;
  hsnSac: string;
  quantity: string;
  unit: string;
  unitPrice: string;
  discount: string;
  ratePct: string;
  itemId: string | null;
  itemSku: string | null;
}

let seq = 0;
export const newLineId = (): string => `line-${++seq}`;

export function blankLine(ratePct = "18"): LineDraft {
  return {
    id: newLineId(),
    description: "",
    hsnSac: "",
    quantity: "1",
    unit: "NOS",
    unitPrice: "",
    discount: "",
    ratePct,
    itemId: null,
    itemSku: null,
  };
}
