import type { InvoiceStatus } from "@/api/types";

/**
 * Invoice lifecycle. Every `InvoiceStatus` word ("paid", "sent", "overdue", "draft", "void") is
 * registered in `@opsgrids/ui`'s status registry, so this is a thin wrapper — no local tones.
 */
const INVOICE_LABEL: Record<InvoiceStatus, string> = {
  paid: "Paid",
  // The API's `sent` means issued and not yet settled: nothing says the document was delivered.
  sent: "Issued",
  overdue: "Overdue",
  draft: "Draft",
  void: "Void",
};

/** The word for a document that is open but has had money allocated to it. */
export function invoiceStatusLabel(
  status: InvoiceStatus,
  amountPaidMinor: number | undefined,
): string {
  const partlyPaid = (amountPaidMinor ?? 0) > 0 && (status === "sent" || status === "overdue");
  if (!partlyPaid) return INVOICE_LABEL[status];
  return status === "overdue" ? "Overdue, partly paid" : "Partly paid";
}
