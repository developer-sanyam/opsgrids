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
export function CommandMenu({ open, onClose, items, placeholder, fallback }: CommandMenuProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const base = useId();
  const listboxId = `${base}-listbox`;

  const matched = useMemo(() => items.filter((item) => matches(item, query)), [items, query]);
  const trimmed = query.trim();
  const displayed = useMemo<CommandMenuItem[]>(() => {
    if (!fallback || trimmed === "") return matched;
    return [
      ...matched,
      {
        key: "fallback-search",
        label: `${fallback.label} “${trimmed}”`,
        group: "Search",
        icon: SearchIcon,
        onSelect: () => fallback.onSubmit(trimmed),
      },
    ];
  }, [matched, fallback, trimmed]);

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

  // Grouped in stable order of first appearance, so "Go to" always leads and "Search" trails.
  const groups: string[] = [];
  for (const item of displayed) if (!groups.includes(item.group)) groups.push(item.group);
  const optionId = (key: string) => `${base}-option-${key}`;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 sm:pt-16">
      <div className="absolute inset-0 bg-scrim" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
        className="relative flex w-full max-w-lg flex-col overflow-hidden rounded-card bg-surface shadow-overlay"
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
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <kbd className="hidden shrink-0 rounded-chip border border-line px-1.5 py-0.5 text-caption font-semibold text-faint sm:block">
            Esc
          </kbd>
        </div>

        <div className="max-h-96 overflow-y-auto p-2">
          {displayed.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted">
              No matches for &ldquo;{query.trim()}&rdquo;.
            </p>
          ) : (
            <ul id={listboxId} role="listbox" aria-label={placeholder}>
              {groups.map((group) => (
                <li key={group}>
                  <p className="px-3 pb-1 pt-2 text-caption font-bold uppercase tracking-wider text-faint">
                    {group}
                  </p>
                  <ul>
                    {displayed
                      .map((item, index) => ({ item, index }))
                      .filter(({ item }) => item.group === group)
                      .map(({ item, index }) => {
                        const Icon = item.icon;
                        return (
                          <li
                            key={item.key}
                            id={optionId(item.key)}
                            role="option"
                            aria-selected={index === activeIndex}
                            onMouseEnter={() => setActiveIndex(index)}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              choose(item);
                            }}
                            className={cn(
                              "flex h-10 cursor-pointer items-center gap-3 rounded-tile px-3 text-sm font-semibold",
                              index === activeIndex
                                ? "bg-accent-soft text-accent-ink"
                                : "text-ink-soft hover:bg-ground",
                            )}
                          >
                            {Icon ? <Icon className="size-4.5 shrink-0" aria-hidden /> : null}
                            <span className="truncate">{item.label}</span>
                          </li>
                        );
                      })}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
