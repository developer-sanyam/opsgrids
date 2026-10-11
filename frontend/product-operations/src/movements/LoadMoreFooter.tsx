import { Button, InlineAlert } from "@/ui";
import { problemMessage } from "@opsgrids/client";

interface LoadMoreFooterProps {
  loaded: number;
  hasMore: boolean;
  loading: boolean;
  error: Error | null;
  onLoadMore: () => void;
  /** Plural, for the sentence: "transfers". */
  noun: string;
}

/**
 * The foot of a cursor-paginated list. The server never says how many rows exist in total, so the
 * line says how many are showing — and, once the cursor runs out, that this is all of them. The
 * line is a polite live region so a screen reader hears the count change when a page lands.
 */
export function LoadMoreFooter({ loaded, hasMore, loading, error, onLoadMore, noun }: LoadMoreFooterProps) {
  return (
    <div className="flex flex-col items-center gap-3 pt-2">
      {error ? (
        <InlineAlert tone="danger" title={`Couldn't load more ${noun}`} className="w-full">
          {problemMessage(error)} The {noun} already shown are unchanged.
        </InlineAlert>
      ) : null}
      <p className="text-sm text-muted" aria-live="polite">
        {hasMore ? `Showing ${loaded} ${noun}` : `Showing all ${loaded} ${noun}`}
      </p>
      {hasMore ? (
        <Button variant="secondary" loading={loading} onClick={onLoadMore}>
          {error ? "Try again" : "Load more"}
        </Button>
      ) : null}
    </div>
  );
}
