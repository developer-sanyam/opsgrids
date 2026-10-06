import { useEffect, useId, useRef, useState } from "react";

import { problemMessage } from "@opsgrids/client";
import { Button, Card, EmptyState, ErrorState, InlineAlert, Skeleton, TextField, WarehouseIcon } from "@opsgrids/ui";

import type { StockRowOut } from "@/api/types";
import { canPutAwayFrom, filterStock, stockDetail, stockLabel } from "@/lib/putawayForm";
import type { AvailableStock } from "@/lib/useAvailableStock";

/** More than this on a phone is a wall of rows; the filter is how a worker gets to the rest. */
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
 *
 * Only stock in a receiving or staging bin is offered (`canPutAwayFrom`): that is the one place a
 * put-away can start, so a row from anywhere else would only ever end in the server's refusal.
 *
 * Each row is a button, not a radio. Choosing stock is what moves the worker on to the next step,
 * so the choice has to be a deliberate press: a radio group selects on the arrow keys, which would
 * commit the first row a keyboard or switch user merely moved past. Tab walks the rows; Enter or
 * Space chooses.
 */
export function PutawayStockPicker({ stock, onSelect, focusSearch = false }: PutawayStockPickerProps) {
  const [filter, setFilter] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();
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

  const eligible = stock.rows.filter(canPutAwayFrom);

  if (eligible.length === 0 && !stock.hasMore) {
    return (
      <Card className="p-2">
        {stock.stale ? <StaleNotice onRefresh={stock.reload} /> : null}
        <EmptyState
          icon={<WarehouseIcon className="size-6" aria-hidden />}
          title="No stock in receiving or staging"
          description="Put-away starts from receiving or staging bins. Stock in any other bin is moved with a bin transfer."
        />
      </Card>
    );
  }

  const matches = filterStock(eligible, filter);
  const shown = matches.slice(0, MAX_SHOWN);

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

      {shown.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p id={listId} className="text-sm font-semibold text-ink-soft">
            Stock to put away
          </p>
          <ul aria-labelledby={listId} className="m-0 flex list-none flex-col gap-2 p-0">
            {shown.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onSelect(row)}
                  className="w-full rounded-tile border border-line p-3 text-left transition-colors hover:bg-ground active:bg-sunken"
                >
                  <span className="block text-sm font-bold text-ink">{stockLabel(row)}</span>
                  <span className="block text-xs text-muted">{stockDetail(row)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-muted" role="status">
          {eligible.length === 0
            ? "Nothing in the stock loaded so far is in a receiving or staging bin. Load more to keep looking."
            : stock.hasMore
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
