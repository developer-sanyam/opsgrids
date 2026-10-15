/** One line as typed. Strings, because they are what the inputs hold; parsed on review. */
export interface LineDraft {
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

export function blankLine(ratePct = "18"): LineDraft {
  return {
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
