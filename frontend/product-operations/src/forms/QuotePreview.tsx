import type { QuoteOut } from "@/api/types";
import { problemMessage } from "@opsgrids/client";
import { formatMoney } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { ErrorState, InlineAlert, Skeleton } from "@/ui";

export type QuotePreviewStatus = "idle" | "loading" | "error" | "success";

interface QuotePreviewProps {
  status: QuotePreviewStatus;
  quote?: QuoteOut | null;
  error?: unknown;
  onRetry?: () => void;
  /** What to say while there is nothing to price yet — a half-filled order form, say. */
  idleMessage?: string;
}

/**
 * `POST /orders/quote`'s answer, rendered live: per-line priced/available state plus the running
 * total. **Never a refusal** — the endpoint itself never 422s over a pricing or availability gap,
 * so this renders "can't price this yet" and "short by N" as ordinary states, not errors, exactly
 * mirroring `fully_priced`/`fully_available` and each line's `priced`/`sufficient` flag.
 *
 * Used both while building a new order (`OrderForm`, as the operator edits lines) and on an
 * existing `DRAFT`/`PLACED` order's detail screen (a live re-check before Place, since pricing or
 * stock may have moved since the order was created) — one component, so the two screens never
 * grow two ways of showing the same shape.
 */
export function QuotePreview({ status, quote, error, onRetry, idleMessage }: QuotePreviewProps) {
  if (status === "idle") {
    return <p className="text-sm text-muted">{idleMessage ?? "Add a line to see pricing and availability."}</p>;
  }
  if (status === "loading") {
    return (
      <div className="flex flex-col gap-2" aria-live="polite" aria-busy="true">
        <span className="sr-only">Pricing the order…</span>
        <Skeleton className="h-14 w-full rounded-tile" />
        <Skeleton className="h-14 w-full rounded-tile" />
      </div>
    );
  }
  if (status === "error") {
    return (
      <ErrorState
        title="Couldn't price this order"
        message={problemMessage(error)}
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  if (!quote) return null;

  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      {!quote.fully_priced ? (
        <InlineAlert tone="warning" title="Some lines can't be priced yet">
          Give the item its own selling price, or type a price for this line — placing the order
          refuses until every line prices.
        </InlineAlert>
      ) : null}
      {!quote.fully_available ? (
        <InlineAlert tone="info" title="Some lines are short on stock right now">
          Placing is still allowed — stock is only reserved once the order is accepted, and this
          can change before then.
        </InlineAlert>
      ) : null}
      <ul className="flex flex-col gap-2">
        {quote.lines.map((line) => (
          <li
            key={line.inventory_item_id}
            className="flex items-center justify-between gap-3 rounded-tile border border-line px-3 py-3 text-sm"
          >
            <div className="flex flex-col leading-tight">
              <span className="font-semibold text-ink">{line.sku || "Unknown item"}</span>
              <span className="text-xs text-faint tnum">
                {formatQty(line.quantity)}
                {" · "}
                {line.sufficient
                  ? `${formatQty(line.available_qty)} available`
                  : `short — only ${formatQty(line.available_qty)} available`}
              </span>
            </div>
            <div className="flex flex-col items-end leading-tight">
              {line.priced ? (
                <span className="tnum font-semibold text-ink">
                  {formatMoney(line.line_total_minor ?? 0, quote.currency)}
                </span>
              ) : (
                <span className="text-xs font-semibold text-late">Not priced</span>
              )}
              {!line.sufficient ? <span className="text-xs font-semibold text-negative">Short</span> : null}
            </div>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between border-t border-line pt-3">
        <span className="text-sm font-bold text-ink">{quote.fully_priced ? "Total" : "Total (partial)"}</span>
        <span className="display text-lg font-extrabold tnum">{formatMoney(quote.total_minor, quote.currency)}</span>
      </div>
    </div>
  );
}
