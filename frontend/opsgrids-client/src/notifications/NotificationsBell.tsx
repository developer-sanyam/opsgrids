/**
 * The header bell every `AppShell`'s `notifications` slot renders — one shared component instead
 * of thirteen copies of the same dropdown. Built on `useNotifications`, beside it.
 *
 * Renders nothing while there is nothing worth showing: the feed hasn't loaded anything yet, or
 * the fetch failed. A bell with an empty or broken drawer behind it is worse than no bell at all
 * (`AppShell`'s own `notifications` docstring, `@opsgrids/ui`).
 *
 * No dependency on `@opsgrids/ui` — neither package depends on the other (see
 * `documents/privateUploads.ts`) — so this draws with `console.css`'s own token classes directly,
 * the same way `ConnectionErrorScreen` does, and redraws the design system's own `BellIcon` glyph
 * inline rather than importing it.
 */

import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { useNotifications } from "./useNotifications";

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function NotificationsBell() {
  const { items, unreadCount, error, hasMore, loadMore, markRead, markAllRead } = useNotifications({
    limit: 8,
  });
  const [open, setOpen] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);

  // The same click-outside idiom `Menu` uses, on the plain `<details>`/`<summary>` disclosure this
  // popover is built on instead of a bespoke roving-focus menu — there is nothing here to choose
  // between, only rows to open and mark read.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (detailsRef.current?.contains(event.target as Node) === true) return;
      setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (error !== null || items.length === 0) return null;

  return (
    <details
      ref={detailsRef}
      open={open}
      onToggle={(event: SyntheticEvent<HTMLDetailsElement>) => setOpen(event.currentTarget.open)}
      className="relative"
    >
      <summary
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"}
        className="grid size-10 list-none place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <span className="relative inline-flex">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.7}
            strokeLinecap="round"
            strokeLinejoin="round"
            width={20}
            height={20}
            aria-hidden="true"
          >
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.7 21a2 2 0 0 1-3.4 0" />
          </svg>
          {unreadCount > 0 ? (
            <span
              aria-live="polite"
              className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-negative px-1 text-caption font-semibold text-on-negative"
            >
              {unreadCount > 9 ? "9+" : unreadCount}
            </span>
          ) : null}
        </span>
      </summary>

      <div
        aria-label="Notifications"
        className="absolute top-full right-0 z-40 mt-1 flex w-80 flex-col rounded-tile border border-line bg-surface py-1 shadow-float"
      >
        <div className="flex items-center justify-between gap-2 px-4 py-2">
          <span className="text-sm font-semibold text-ink">Notifications</span>
          {unreadCount > 0 ? (
            <button
              type="button"
              onClick={() => void markAllRead()}
              className="text-caption font-semibold text-accent-ink hover:underline"
            >
              Mark all read
            </button>
          ) : null}
        </div>
        <ul className="flex max-h-96 flex-col overflow-y-auto">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => void markRead(item.id)}
                className="flex w-full flex-col items-start gap-1 px-4 py-2 text-left transition-colors hover:bg-ground focus-visible:bg-ground"
              >
                <span className="flex w-full items-center gap-2">
                  <span
                    aria-hidden
                    className={
                      item.read_at === null
                        ? "size-2 shrink-0 rounded-full bg-accent"
                        : "size-2 shrink-0 rounded-full bg-transparent"
                    }
                  />
                  <span
                    className={
                      item.read_at === null
                        ? "flex-1 truncate text-sm font-semibold text-ink"
                        : "flex-1 truncate text-sm font-medium text-ink-soft"
                    }
                  >
                    {item.subject ?? item.body}
                  </span>
                  {item.read_at === null ? <span className="sr-only">(unread)</span> : null}
                </span>
                {item.subject ? (
                  <span className="line-clamp-2 pl-4 text-caption text-muted">{item.body}</span>
                ) : null}
                <span className="pl-4 text-caption text-faint">{formatWhen(item.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
        {hasMore ? (
          <button
            type="button"
            onClick={loadMore}
            className="px-4 py-2 text-left text-caption font-semibold text-accent-ink hover:underline"
          >
            Load more
          </button>
        ) : null}
      </div>
    </details>
  );
}
