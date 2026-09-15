import type { InvoiceStatus, PaymentMethod } from "@/api/types";
import { cn } from "@opsgrids/ui";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/**
 * Invoice lifecycle, mapped to the semantic pill families:
 * Paid = green, Sent = blue, Overdue = red, Draft = grey, Void = muted with a strike.
 * A coloured dot backs up the colour so status never rests on hue alone.
 */
const INVOICE_TONE: Record<InvoiceStatus, string> = {
  paid: "bg-present-soft text-present",
  sent: "bg-info-soft text-info",
  overdue: "bg-danger-soft text-danger",
  draft: "bg-ground text-muted",
  void: "bg-ground text-faint",
};

const INVOICE_LABEL: Record<InvoiceStatus, string> = {
  paid: "Paid",
  sent: "Sent",
  overdue: "Overdue",
  draft: "Draft",
  void: "Void",
};

export function InvoiceStatusPill({ status }: { status: InvoiceStatus }) {
  return (
    <span className={cn(PILL_BASE, INVOICE_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      <span className={status === "void" ? "line-through" : undefined}>
        {INVOICE_LABEL[status]}
      </span>
    </span>
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
