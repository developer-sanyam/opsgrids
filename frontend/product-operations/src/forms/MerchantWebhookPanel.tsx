import type { MerchantAccountOut } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { providerName } from "@/lib/paymentProviders";
import { CopyField } from "@/ui";

/** The absolute URL to register: the server's own (built from the platform's public address),
 *  or — from a server that does not send one — this page's origin and the path. */
function webhookUrl(account: MerchantAccountOut): string | null {
  if (account.webhook_url) return account.webhook_url;
  return account.webhook_path ? `${window.location.origin}${account.webhook_path}` : null;
}

function Steps({ account }: { account: MerchantAccountOut }) {
  if (account.provider === "cashfree") {
    return (
      <ol className="flex list-decimal flex-col gap-1 pl-4 text-sm text-ink-soft">
        <li>In Cashfree, open Developers → Webhooks and add this URL as a Payment Gateway endpoint.</li>
        <li>Choose webhook version 2025-01-01 or later, and subscribe to the events below.</li>
        <li>
          There is no webhook secret to copy: Cashfree signs every delivery with your Secret Key, and this platform checks
          it with the one you saved here.
        </li>
        <li>
          For Production, also add this platform's address under Developers → Whitelisting, or Cashfree will refuse to
          open its checkout here.
        </li>
      </ol>
    );
  }
  return (
    <ol className="flex list-decimal flex-col gap-1 pl-4 text-sm text-ink-soft">
      <li>In Razorpay, open Settings → Webhooks and add this URL.</li>
      <li>Set its secret to the webhook secret you saved here, and enable the events below.</li>
    </ol>
  );
}

/**
 * Where the merchant's provider tells this platform about payments: the URL to register (with a
 * copy button), the steps in that provider's own dashboard, the events to enable, and when a
 * webhook last arrived — the one way to see "is my webhook reaching you?" without asking anyone.
 * A missing webhook is not fatal (status checks still settle every payment), only slower.
 */
export function MerchantWebhookPanel({ account }: { account: MerchantAccountOut }) {
  const url = webhookUrl(account);
  const name = providerName(account.provider);
  return (
    <section className="flex flex-col gap-3" aria-label="Webhook">
      {url ? <CopyField label="Webhook URL" value={url} /> : null}
      <Steps account={account} />
      <div className="flex flex-col gap-1">
        <span className="text-sm text-muted">Events to enable on that webhook</span>
        <span className="font-mono text-xs text-ink-soft">{account.webhook_events.join(", ")}</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm text-muted">Last webhook received</span>
        {account.last_webhook_at ? (
          <span className="tnum text-sm text-ink">{formatDateTime(account.last_webhook_at)}</span>
        ) : (
          <span className="text-sm text-ink-soft">
            None yet. Payments are still confirmed by asking {name} directly; add the webhook so they are confirmed sooner.
          </span>
        )}
      </div>
    </section>
  );
}
