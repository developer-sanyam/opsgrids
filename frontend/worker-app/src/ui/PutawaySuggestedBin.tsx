import { Button, InlineAlert, Skeleton } from "@opsgrids/ui";

import type { PutawaySuggestion, PutawaySuggestionReason } from "@/api/types";

const REASON: Record<PutawaySuggestionReason, string> = {
  holds_item: "Already holds this item",
  empty_bin: "Empty bin",
};

interface PutawaySuggestedBinProps {
  status: "loading" | "success" | "error";
  suggestion: PutawaySuggestion | null;
  onRetry: () => void;
}

/**
 * Where the system would put this stock. A suggestion is help, never a gate: if it can't be loaded
 * or there isn't one, the worker is told so and carries on by scanning whichever bin they chose.
 */
export function PutawaySuggestedBin({ status, suggestion, onRetry }: PutawaySuggestedBinProps) {
  if (status === "loading") return <Skeleton className="h-20 w-full rounded-tile" />;

  if (status === "error") {
    return (
      <InlineAlert
        tone="warning"
        title="Couldn't load a bin suggestion"
        action={
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        }
      >
        You can still scan any bin.
      </InlineAlert>
    );
  }

  if (suggestion === null) {
    return (
      <p className="rounded-tile bg-ground p-3 text-sm text-muted">
        No bin is suggested for this stock. Scan the bin you are putting it in.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1 rounded-tile bg-ground p-3">
      <p className="text-xs font-semibold text-muted">Suggested bin</p>
      <p className="font-mono text-lg font-bold text-ink">{suggestion.code}</p>
      {suggestion.name ? <p className="text-sm text-ink">{suggestion.name}</p> : null}
      {suggestion.path ? <p className="text-xs text-muted">{suggestion.path}</p> : null}
      <p className="text-xs text-muted">{REASON[suggestion.reason]}</p>
      {suggestion.barcode === null ? (
        <InlineAlert tone="warning" title="This bin has no scannable label">
          The system can't recognise a bin without one. Ask a manager to add a barcode to it, or scan a different bin.
        </InlineAlert>
      ) : suggestion.barcode !== suggestion.code ? (
        <p className="text-xs text-muted">
          Its label reads <span className="font-mono font-semibold text-ink">{suggestion.barcode}</span>
        </p>
      ) : null}
    </div>
  );
}
