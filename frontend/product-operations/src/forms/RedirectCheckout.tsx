import type { RedirectOrderCheckoutSessionOut } from "@/api/types";
import { formatMoneyPrecise } from "@/lib/format";
import { providerName } from "@/lib/paymentProviders";
import { Button, InlineAlert } from "@/ui";

/**
 * A redirect-checkout provider's page (PhonePe), opened in a new tab by a real click — a `GET` of
 * the URL, or a form `POST` of its fields — so this order page stays open, keeps asking the
 * provider what happened, and is never navigated away from. The URL and fields carry a short-lived
 * token: they live in this component's props only, never in storage or the address bar.
 */
export function RedirectCheckout({ session }: { session: RedirectOrderCheckoutSessionOut }) {
  const name = providerName(session.provider);
  const label = `Open ${name} checkout`;
  return (
    <InlineAlert tone="info" title={`Pay ${formatMoneyPrecise(session.amount_minor, session.currency)} on ${name}`}>
      <div className="flex flex-col gap-2">
        <p className="text-sm">
          {name}'s checkout opens in a new tab. The customer pays there; this page updates once {name} confirms it.
        </p>
        {session.method === "POST" ? (
          <form method="post" action={session.url} target="_blank" rel="noopener noreferrer">
            {Object.entries(session.fields).map(([key, value]) => (
              <input key={key} type="hidden" name={key} value={value} />
            ))}
            <Button type="submit">{label}</Button>
          </form>
        ) : (
          <div>
            <Button onClick={() => window.open(session.url, "_blank", "noopener,noreferrer")}>{label}</Button>
          </div>
        )}
      </div>
    </InlineAlert>
  );
}
