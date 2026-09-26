import type { InvoiceStatus, PaymentMethod } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";

/**
 * Invoice lifecycle. Every `InvoiceStatus` word ("paid", "sent", "overdue", "draft", "void") is
 * registered in `@opsgrids/ui`'s status registry, so this is a thin wrapper — no local tones.
 */
const INVOICE_LABEL: Record<InvoiceStatus, string> = {
  paid: "Paid",
  sent: "Sent",
  overdue: "Overdue",
  draft: "Draft",
  void: "Void",
};

export function InvoiceStatusPill({ status }: { status: InvoiceStatus }) {
  return (
    <StatusBadge
      status={status}
      label={INVOICE_LABEL[status]}
      className={status === "void" ? "line-through" : undefined}
    />
  );
}

const METHOD_LABEL: Record<PaymentMethod, string> = {
  bank_transfer: "Bank transfer",
  upi: "UPI",
  card: "Card",
  cheque: "Cheque",
  cash: "Cash",
};

/** A quiet neutral badge naming how a payment arrived. */
export function PaymentMethodBadge({ method }: { method: PaymentMethod }) {
  return (
    <span className="inline-flex items-center rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-ink-soft">
      {METHOD_LABEL[method]}
    </span>
  );
}
