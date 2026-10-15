import type { InvoiceStatus, PaymentMethod } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";

import { invoiceStatusLabel } from "./invoiceStatusLabel";

/** Invoice lifecycle on the shared badge; every status word is in `@opsgrids/ui`'s registry, so no local tones. */
export function InvoiceStatusPill({
  status,
  amountPaidMinor,
}: {
  status: InvoiceStatus;
  /** Money settled so far; lets an open document say it is partly paid. */
  amountPaidMinor?: number;
}) {
  return (
    <StatusBadge
      status={status}
      label={invoiceStatusLabel(status, amountPaidMinor)}
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
