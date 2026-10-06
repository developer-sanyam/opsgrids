import type { ReactNode } from "react";

import { InlineAlert } from "@opsgrids/ui";

import { COPY } from "./paymentCopy";
import type { NoticeCopy } from "./paymentCopy";

/**
 * The one polite live region a payment screen announces through. It is rendered — empty — before
 * anything happens, because a region that appears together with its first message is one many
 * screen readers never announce. The alerts inside stay silent (`live="off"`) so nothing speaks
 * twice.
 *
 * `testMode` adds the test-mode notice under the payment's own, inside the same region: the
 * region is atomic, so it is read out with every change of the payment, never on its own.
 */
export function PaymentNotice({
  copy,
  context,
  action,
  testMode = false,
}: {
  copy: NoticeCopy | null;
  /** "Invoice INV-0042 · ₹4,999.00" — which payment this is about. */
  context?: string | undefined;
  action?: ReactNode | undefined;
  /** The checkout's `test_mode`: its provider runs on test or sandbox credentials. */
  testMode?: boolean | undefined;
}) {
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className="flex flex-col gap-2">
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
      {copy && testMode ? <InlineAlert tone={COPY.testMode.tone} title={COPY.testMode.title} live="off" /> : null}
    </div>
  );
}
