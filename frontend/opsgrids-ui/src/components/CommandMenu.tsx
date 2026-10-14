import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ComponentType, KeyboardEvent, SVGProps } from "react";
import { createPortal } from "react-dom";

import { SearchIcon } from "../icons";
import { cn } from "../lib/cn";
import { useFocusTrap } from "../lib/useFocusTrap";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface CommandMenuItem {
  key: string;
  label: string;
  /** The section it lists under: "Go to", "Modules", "Quick actions". */
  group: string;
  icon?: IconType | undefined;
  onSelect: () => void;
}

/**
 * Rows the app fetches for the typed query (`useGlobalSearch`), listed after the matched static
 * items under their own `group`. The menu only reports the query; debouncing and cancelling are
 * the hook's.
 */
export interface CommandMenuResults {
  /** Called with the trimmed query on every change, including "" when the menu opens. */
  onQueryChange: (query: string) => void;
  status: "idle" | "loading" | "success" | "error";
  items: CommandMenuItem[];
  /** Some sources failed: the rows are real but may be incomplete. */
  partial: boolean;
  /** Said in place of the status, e.g. why an email or phone is not searched here. */
  hint?: string | null | undefined;
}

/** The one sentence the status line says, or "" when there is nothing to say. */
function resultsStatus(results: CommandMenuResults, query: string): string {
  if (results.hint) return results.hint;
  if (query === "") return "";
  if (results.status === "loading") return "Searching…";
  if (results.status === "error" || results.partial) return "Some results may be missing";
  if (results.status === "success" && results.items.length === 0) return `No matches for “${query}”.`;
  return "";
}

interface CommandMenuProps {
  open: boolean;
  onClose: () => void;
  items: CommandMenuItem[];
  placeholder: string;
  /**
   * The last resort when nothing above is what was meant: "Search deals for “acme”" against the
   * app's own `?q=` list route (the pattern every sidebar search used before this palette existed —
   * the report's API gaps: there is no global search endpoint yet). Shown once something has been
   * typed, after every matched item, never in place of them.
   */
  fallback?: { label: string; onSubmit: (query: string) => void } | undefined;
  /** Server results for the query, with a polite status line. Omitted, the menu is unchanged. */
  results?: CommandMenuResults | undefined;
}

/** Case-insensitive substring match on the label and its group, so typing "crm" finds a "CRM"
 *  module even if the label alone doesn't contain it. */
function matches(item: CommandMenuItem, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  return item.label.toLowerCase().includes(needle) || item.group.toLowerCase().includes(needle);
}

/**
 * The ⌘K palette: every nav item, cross-product module and quick action the shell knows about, in
 * one keyboard-driven list. Typing filters; Up/Down moves the highlighted option (wrapping),
 * Enter runs it and closes, Escape closes without acting.
 *
 * The input keeps real focus throughout — options are named by `aria-activedescendant`, the same
 * pattern `Combobox` uses, rather than each option taking DOM focus in turn. There is no
 * server-backed search yet (the report's API gaps): v1 is navigation over what the shell already
 * has, not a query against the backend.
 */
export function CommandMenu({ open, onClose, items, placeholder, fallback, results }: CommandMenuProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const base = useId();
  const listboxId = `${base}-listbox`;

  const matched = useMemo(() => items.filter((item) => matches(item, query)), [items, query]);
  const trimmed = query.trim();
  const resultItems = results?.items;
  const displayed = useMemo<CommandMenuItem[]>(() => {
    const listed = trimmed === "" || resultItems === undefined ? matched : [...matched, ...resultItems];
    if (!fallback || trimmed === "") return listed;
    return [
      ...listed,
      {
        key: "fallback-search",
        label: `${fallback.label} “${trimmed}”`,
        group: "Search",
        icon: SearchIcon,
        onSelect: () => fallback.onSubmit(trimmed),
      },
    ];
  }, [matched, resultItems, fallback, trimmed]);

  // A ref, so a new `onQueryChange` identity on every render does not re-send the same query.
  const onQueryChange = useRef(results?.onQueryChange);
  onQueryChange.current = results?.onQueryChange;
  useEffect(() => {
    onQueryChange.current?.(trimmed);
  }, [trimmed]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  useFocusTrap({ open, panelRef, onClose, initialFocusRef: inputRef, lockBodyScroll: true });

  if (!open) return null;

  function choose(item: CommandMenuItem) {
    onClose();
    item.onSelect();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (displayed.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % displayed.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + displayed.length) % displayed.length);
    } else if (event.key === "Enter") {
      const item = displayed[activeIndex];
      if (item) {
        event.preventDefault();
        choose(item);
      }
    }
  }

  const statusText = results !== undefined ? resultsStatus(results, trimmed) : "";

  // Grouped in stable order of first appearance, so "Go to" always leads and "Search" trails.
  const groups: string[] = [];
  for (const item of displayed) if (!groups.includes(item.group)) groups.push(item.group);
  const optionId = (key: string) => `${base}-option-${key}`;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center sm:p-4 sm:pt-16">
      <div className="absolute inset-0 bg-scrim" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
        className="sheet-safe relative flex h-dvh w-full max-w-lg flex-col overflow-hidden bg-surface shadow-overlay sm:h-auto sm:rounded-card"
      >
        <div className="shell-header-h flex items-center gap-3 border-b border-line px-4">
          <SearchIcon className="size-5 shrink-0 text-faint" aria-hidden />
          <label htmlFor={`${base}-input`} className="sr-only">
            {placeholder}
          </label>
          <input
            id={`${base}-input`}
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded={true}
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={displayed[activeIndex] ? optionId(displayed[activeIndex].key) : undefined}
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            className="min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <kbd className="hidden shrink-0 rounded-chip border border-line px-1.5 py-0.5 text-caption font-semibold text-faint sm:block">
            Esc
          </kbd>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2 sm:max-h-96 sm:flex-none">
          {displayed.length === 0 && results === undefined ? (
            <p className="px-3 py-6 text-center text-sm text-muted">
              No matches for &ldquo;{query.trim()}&rdquo;.
            </p>
          ) : displayed.length === 0 ? null : (
            <div id={listboxId} role="listbox" aria-label={placeholder}>
              {groups.map((group) => (
                <div key={group} role="group" aria-label={group}>
                  <p aria-hidden className="px-3 pb-1 pt-2 text-caption font-bold uppercase tracking-wider text-faint">
                    {group}
                  </p>
                  <div>
                    {displayed
                      .map((item, index) => ({ item, index }))
                      .filter(({ item }) => item.group === group)
                      .map(({ item, index }) => {
                        const Icon = item.icon;
                        return (
                          <div
                            key={item.key}
                            id={optionId(item.key)}
                            role="option"
                            // Named by the input's aria-activedescendant; the input keeps real focus.
                            tabIndex={-1}
                            aria-selected={index === activeIndex}
                            onMouseEnter={() => setActiveIndex(index)}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              choose(item);
                            }}
                            className={cn(
                              "flex h-11 cursor-pointer items-center gap-3 rounded-tile px-3 text-sm font-semibold",
                              index === activeIndex
                                ? "bg-accent-soft text-accent-ink"
                                : "text-ink-soft hover:bg-ground",
                            )}
                          >
                            {Icon ? <Icon className="size-4.5 shrink-0" aria-hidden /> : null}
                            <span className="truncate">{item.label}</span>
                          </div>
                        );
                      })}
                  </div>
                </div>
              ))}
            </div>
          )}
          {results !== undefined ? (
            // Always mounted while results are wired: a live region must exist before its text does.
            <p role="status" className={cn("px-3 text-xs text-muted", statusText !== "" && "py-2")}>
              {statusText}
            </p>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
