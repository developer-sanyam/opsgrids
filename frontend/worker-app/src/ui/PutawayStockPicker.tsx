import { useEffect, useRef, useState } from "react";

import { problemMessage } from "@opsgrids/client";
import { Button, Card, EmptyState, ErrorState, InlineAlert, RadioGroup, Skeleton, TextField, WarehouseIcon } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import type { StockRowOut } from "@/api/types";
import { filterStock, stockDetail, stockLabel } from "@/lib/putawayForm";
import type { AvailableStock } from "@/lib/useAvailableStock";

/** More than this on a phone is a wall of radios; the filter is how a worker gets to the rest. */
const MAX_SHOWN = 30;

/** The list on screen is the last one that loaded, because asking again failed — shown rather than
 *  an error, so a worker with a weak signal can still enter the next line of what they carry. */
function StaleNotice({ onRefresh }: { onRefresh: () => void }) {
  return (
    <InlineAlert
      tone="warning"
      title="Couldn't refresh the stock list"
      action={
        <Button variant="secondary" size="sm" onClick={onRefresh}>
          Refresh
        </Button>
      }
    >
      Showing the list from earlier, so quantities may be out of date.
    </InlineAlert>
  );
}

interface PutawayStockPickerProps {
  stock: AvailableStock;
  onSelect: (row: StockRowOut) => void;
  /** Put the cursor in the search as the list appears — for a worker who came back to change their
   *  pick, not on first arrival, where it would raise a phone's keyboard before they had asked. */
  focusSearch?: boolean | undefined;
}

/**
 * Step one: which stock is being put away. Searchable over what has loaded (SKU, item name, lot,
 * roll, bin), with its own loading, empty and error states — a worker holding a pallet is waiting
 * on this list, so it never shows a blank card.
 */
export function PutawayStockPicker({ stock, onSelect, focusSearch = false }: PutawayStockPickerProps) {
  const [filter, setFilter] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const searchVisible = stock.status === "success";
  useEffect(() => {
    if (focusSearch && searchVisible) searchRef.current?.focus();
  }, [focusSearch, searchVisible]);

  if (stock.status === "loading") {
    return (
      <Card className="flex flex-col gap-3 p-4">
        <Skeleton className="h-11 w-full rounded-tile" />
        {[0, 1, 2].map((row) => (
          <Skeleton key={row} className="h-16 w-full rounded-tile" />
        ))}
      </Card>
    );
  }

  if (stock.status === "error") {
    return (
      <Card className="p-2">
        <ErrorState
          title="Couldn't load stock"
          message={stock.error ? problemMessage(stock.error) : "Something went wrong. Try again."}
          onRetry={stock.reload}
        />
      </Card>
    );
  }

  if (stock.rows.length === 0 && !stock.hasMore) {
    return (
      <Card className="p-2">
        {stock.stale ? <StaleNotice onRefresh={stock.reload} /> : null}
        <EmptyState
          icon={<WarehouseIcon className="size-6" aria-hidden />}
          title="No stock to put away"
          description="Nothing available is waiting in receiving or staging. Stock appears here once it has been received or moved there."
        />
      </Card>
    );
  }

  const matches = filterStock(stock.rows, filter);
  const options: RadioOption[] = matches.slice(0, MAX_SHOWN).map((row) => ({
    value: row.id,
    label: stockLabel(row),
    description: stockDetail(row),
  }));

  return (
    <Card className="flex flex-col gap-4 p-4">
      {stock.stale ? <StaleNotice onRefresh={stock.reload} /> : null}
      <TextField
        ref={searchRef}
        label="Find stock"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="SKU, item, lot, roll or bin"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        helper="Pick the stock you are carrying. It must be in a receiving or staging bin."
      />

      {options.length > 0 ? (
        <RadioGroup
          legend="Stock to put away"
          name="putaway-stock"
          value={null}
          onChange={(id) => {
            const row = matches.find((candidate) => candidate.id === id);
            if (row) onSelect(row);
          }}
          options={options}
        />
      ) : (
        <p className="text-sm text-muted" role="status">
          {stock.hasMore
            ? "Nothing in the stock loaded so far matches. Load more to keep looking."
            : "Nothing matches that. Check the spelling, or clear the search."}
        </p>
      )}

      {matches.length > MAX_SHOWN ? (
        <p className="text-xs text-muted">
          Showing {MAX_SHOWN} of {matches.length} matches. Type more to narrow it down.
        </p>
      ) : null}

      {stock.loadMoreError ? <InlineAlert tone="danger" title={problemMessage(stock.loadMoreError)} /> : null}
      {stock.hasMore ? (
        <Button variant="secondary" onClick={stock.loadMore} loading={stock.loadingMore}>
          Load more stock
        </Button>
      ) : null}
    </Card>
  );
}
