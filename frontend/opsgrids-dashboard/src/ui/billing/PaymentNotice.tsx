import type { ReactNode } from "react";

import { InlineAlert } from "@opsgrids/ui";

import type { NoticeCopy } from "./paymentCopy";

/**
 * The one polite live region a payment screen announces through. It is rendered — empty — before
 * anything happens, because a region that appears together with its first message is one many
 * screen readers never announce. The alert inside stays silent (`live="off"`) so nothing speaks
 * twice.
 */
export function PaymentNotice({
  copy,
  context,
  action,
}: {
  copy: NoticeCopy | null;
  /** "Invoice INV-0042 · ₹4,999.00" — which payment this is about. */
  context?: string | undefined;
  action?: ReactNode | undefined;
}) {
  return (
    <div role="status" aria-live="polite" aria-atomic="true">
      {copy ? (
        <InlineAlert tone={copy.tone} title={copy.title} live="off" action={action}>
          {copy.detail || context ? (
            <>
              {context ? <span className="tnum block font-semibold text-ink">{context}</span> : null}
              {copy.detail ? <span className="block">{copy.detail}</span> : null}
            </>
          ) : null}
        </InlineAlert>
      ) : null}
    </div>
  );
}
