import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import { ChevronDown, MoreHorizontalIcon } from "../icons";
import { cn } from "../lib/cn";
import { Button } from "./Button";
import type { ButtonSize, ButtonVariant } from "./Button";

export interface MenuItem {
  /** The verb, as the user reads it: "Export CSV", "Suspend account". Also what typeahead matches. */
  label: string;
  onSelect: () => void;
  icon?: ReactNode | undefined;
  /** `danger` tints the label red. The label still carries the meaning on its own. */
  tone?: "default" | "danger" | undefined;
  /** Leave it out entirely — the permission answer. Gate it with `useCan` from `@opsgrids/client`. */
  hidden?: boolean | undefined;
  /** Present but not available now. Requires `disabledReason`: a dead item needs a why. */
  disabled?: boolean | undefined;
  disabledReason?: string | undefined;
}

interface MenuProps {
  /** The trigger's name and the menu's: "Export", "Actions for Acme Ltd". */
  label: string;
  items: MenuItem[];
  /**
   * `label` (default): the trigger is a `Button` showing the label and a chevron. `icon`: a round
   * icon button — the ⋯ by default — with the label as its accessible name, for a card header or
   * a toolbar short on room.
   */
  trigger?: "label" | "icon" | undefined;
  /** The trigger's leading icon, or its only icon when `trigger="icon"`. */
  icon?: ReactNode | undefined;
  /** The label trigger's `Button` variant. Default `outline`: opening a menu is not the primary action. */
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  /** Which edge of the trigger the menu lines up with. `end` for a trigger at a row's right. */
  align?: "start" | "end" | undefined;
  className?: string | undefined;
}

/** How long typed characters keep accumulating into one search: "ex" finds "Export". */
const TYPEAHEAD_MS = 500;

/**
 * A menu of actions behind one button — the dropdown the brief lists (§18), for anything that is
 * not a table row's actions (`RowActions` owns those).
 *
 * **The WAI-ARIA menu button pattern, whole.** Enter, Space or Down on the trigger opens the menu
 * on its first item; Up opens it on the last. Inside, Up and Down move and wrap, Home and End
 * jump, and typing a letter or two moves to the next item that starts with them. Escape closes
 * and puts focus back on the trigger; Tab closes and lets focus move on; a click outside closes.
 *
 * **Choosing an item returns focus to the trigger before the action runs**, so a dialog the action
 * opens records the trigger as where focus goes back to — not the menu item it just unmounted.
 *
 * Disabled items stay focusable and read their reason, for the same reason `RowActions`' do: a
 * `disabled` control is skipped by the keyboard and its explanation is never heard.
 */
export function Menu({
  label,
  items,
  trigger = "label",
  icon,
  variant = "outline",
  size = "sm",
  align = "start",
  className,
}: MenuProps) {
  const visible = items.filter((item) => item.hidden !== true);
  const [open, setOpen] = useState(false);
  /** Which item takes focus when the menu opens: the first, or the last (opened with Up). */
  const [entry, setEntry] = useState<"first" | "last">("first");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const base = useId();
  const triggerId = `${base}-trigger`;
  const menuId = `${base}-menu`;

  function menuItems(): HTMLButtonElement[] {
    return Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role='menuitem']") ?? []);
  }

  useEffect(() => {
    if (!open) return;
    const list = menuItems();
    (entry === "last" ? list[list.length - 1] : list[0])?.focus();

    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) === true) return;
      if (triggerRef.current?.contains(target) === true) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, entry]);

  function openAt(where: "first" | "last") {
    setEntry(where);
    setOpen(true);
  }

  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openAt("first");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openAt("last");
    }
  }

  function typeahead(key: string, list: HTMLButtonElement[], index: number) {
    const now = Date.now();
    const text = now - typed.current.at > TYPEAHEAD_MS ? key : typed.current.text + key;
    typed.current = { text, at: now };
    const query = text.toLowerCase();
    // Search from the item after the current one, wrapping — pressing "s" twice walks through
    // "Suspend" and then "Send invoice" rather than sticking on the first.
    const start = text.length === 1 ? index + 1 : index;
    for (let offset = 0; offset < list.length; offset += 1) {
      const candidate = list[(start + offset) % list.length];
      if (candidate?.dataset["label"]?.toLowerCase().startsWith(query) === true) {
        candidate.focus();
        return;
      }
    }
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const list = menuItems();
    const index = list.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === "Tab") {
      close(false);
      return;
    }
    if (list.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      list[(index + 1) % list.length]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      list[(index - 1 + list.length) % list.length]?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      list[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      list[list.length - 1]?.focus();
    } else if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      typeahead(event.key, list, index);
    }
  }

  function choose(item: MenuItem) {
    if (item.disabled === true) return;
    close(true);
    item.onSelect();
  }

  if (visible.length === 0) return null;

  const triggerProps = {
    ref: triggerRef,
    id: triggerId,
    "aria-haspopup": "menu" as const,
    "aria-expanded": open,
    "aria-controls": open ? menuId : undefined,
    onClick: () => (open ? close(false) : openAt("first")),
    onKeyDown: onTriggerKeyDown,
  };

  return (
    // `h-fit`: a flex parent must not stretch this wrapper, or `top-full` would hang the menu
    // from the bottom of the stretched box instead of from the trigger.
    <div className={cn("relative inline-flex h-fit", className)}>
      {trigger === "icon" ? (
        <button
          type="button"
          aria-label={label}
          {...triggerProps}
          className="hit-44 relative grid size-9 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
        >
          {icon ?? <MoreHorizontalIcon className="size-5" />}
        </button>
      ) : (
        <Button
          variant={variant}
          size={size}
          leadingIcon={icon}
          trailingIcon={<ChevronDown className="size-4" aria-hidden />}
          {...triggerProps}
        >
          {label}
        </Button>
      )}
      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-labelledby={triggerId}
          // Focusable by script, not by Tab — focus lives on the items (WAI-ARIA menu pattern);
          // this lets the container own the key handler above.
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
          className={cn(
            // A phone gets a bottom sheet (a popover at the trigger would leave the screen); from
            // 768px it hangs from the trigger.
            "sheet-safe-bottom fixed inset-x-2 bottom-2 z-40 flex max-h-3/4 flex-col overflow-y-auto",
            "rounded-card border border-line bg-surface py-1 shadow-overlay",
            "md:absolute md:inset-x-auto md:bottom-auto md:top-full md:mt-1 md:min-w-48 md:max-h-none md:rounded-tile md:shadow-float",
            align === "end" ? "md:right-0" : "md:left-0",
          )}
        >
          {visible.map((item, index) => {
            const reasonId = item.disabled === true && item.disabledReason ? `${base}-reason-${index}` : undefined;
            return (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                tabIndex={-1}
                data-label={item.label}
                aria-disabled={item.disabled === true || undefined}
                // Named by its label alone; the reason inside it is the description, not the name.
                {...(reasonId !== undefined ? { "aria-label": item.label, "aria-describedby": reasonId } : {})}
                title={item.disabled === true ? item.disabledReason : undefined}
                onClick={() => choose(item)}
                className={cn(
                  // 44px rows: a menu is where the destructive action lives, and thumbs are wide.
                  "flex min-h-11 items-center gap-3 px-4 py-2 text-left text-sm font-medium whitespace-nowrap",
                  "transition-colors focus-visible:-outline-offset-2",
                  item.disabled === true
                    ? "cursor-not-allowed text-muted"
                    : item.tone === "danger"
                      ? "text-negative hover:bg-negative-soft focus-visible:bg-negative-soft"
                      : "text-ink-soft hover:bg-ground focus-visible:bg-ground",
                )}
              >
                {item.icon ? (
                  <span aria-hidden className="grid size-5 place-items-center">
                    {item.icon}
                  </span>
                ) : null}
                {item.label}
                {reasonId !== undefined ? (
                  <span id={reasonId} className="sr-only">
                    {item.disabledReason}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
