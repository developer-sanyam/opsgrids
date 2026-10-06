import type { ReactNode } from "react";

import { problemMessage } from "@opsgrids/client";
import { Button, DataTable, ErrorState } from "@/ui";
import type { Column, TableSelection } from "@/ui";
import type { CursorList } from "./useCursorList";

interface CursorTableProps<T> {
  list: CursorList<T>;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  caption: string;
  /** What the rows are, plural: "lots", "ledger rows". Used in the footer and the announcements. */
  noun: string;
  emptyState: ReactNode;
  /** The failed first page's heading: "Couldn't load lots". */
  errorTitle: string;
  onRowClick?: ((row: T) => void) | undefined;
  selection?: TableSelection<T> | undefined;
  bulkActions?: ReactNode | undefined;
  toolbar?: ReactNode | undefined;
}

/**
 * A cursor-paginated table with all four states: skeleton rows holding the body's place while the
 * first page loads, an error with a retry, an empty state that says what to do next, and the rows
 * with a "Load more" that appends the next page.
 *
 * There is no total on a keyset page, so the footer never claims one: it says how many rows are
 * showing, and "Showing all N" only once the server has stopped naming a next page. The count is a
 * polite live region, so a screen reader hears that more arrived.
 */
export function CursorTable<T>({
  list,
  columns,
  rowKey,
  caption,
  noun,
  emptyState,
  errorTitle,
  onRowClick,
  selection,
  bulkActions,
  toolbar,
}: CursorTableProps<T>) {
  const failed = list.status === "error";
  return (
    <div className="flex flex-col gap-3">
      <DataTable
        columns={columns}
        rows={list.items}
        rowKey={rowKey}
        caption={caption}
        loading={list.status === "loading"}
        loadingRows={6}
        emptyState={emptyState}
        errorState={
          failed ? (
            <ErrorState title={errorTitle} message={problemMessage(list.error)} onRetry={list.reload} />
          ) : undefined
        }
        mobileCard="auto"
        {...(onRowClick ? { onRowClick } : {})}
        {...(selection ? { selection } : {})}
        {...(bulkActions ? { bulkActions } : {})}
        {...(toolbar ? { toolbar } : {})}
      />
      <div className="flex flex-col items-center gap-2 pb-2" aria-live="polite">
        {list.status === "ready" && list.items.length > 0 ? (
          <p className="tnum text-sm text-muted">
            {list.hasMore ? `Showing ${list.items.length} ${noun}` : `Showing all ${list.items.length} ${noun}`}
          </p>
        ) : null}
        {list.moreError ? (
          <p role="alert" className="text-sm text-negative">
            {problemMessage(list.moreError, "Couldn't load more.")}
          </p>
        ) : null}
        {list.hasMore ? (
          <Button variant="outline" onClick={list.loadMore} disabled={list.loadingMore}>
            {list.loadingMore ? "Loading…" : list.moreError ? "Try again" : "Load more"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
