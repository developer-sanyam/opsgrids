import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { cn } from "../lib/cn";
import { MoreHorizontalIcon } from "../icons";

export interface RowAction {
  /** The verb, as the user reads it: "View", "Edit", "Delete". */
  label: string;
  onSelect: () => void;
  /** `danger` tints the label red. The label still carries the meaning on its own. */
  tone?: "default" | "danger";
  /**
   * Leave the action out entirely. This is the **permission** answer — an absent control is
   * honest, and a Delete button that is present but refuses is a puzzle. Gate it with
   * `useCan` from `@opsgrids/client`.
   */
  hidden?: boolean;
  /**
   * Present but not available *right now* — an invoice that is already paid, a delegation
   * already revoked. Requires `disabledReason`, because a dead control with no explanation is
   * the thing this prop exists to avoid.
   */
  disabled?: boolean;
  /** Why it cannot be used. Shown on hover and read by a screen reader. */
  disabledReason?: string;
}

interface RowActionsProps {
  actions: RowAction[];
  /**
   * What the row *is*, for the accessible name: "Acme Ltd" gives "Edit Acme Ltd". Without it
   * every row in the table has a button called "Edit" and a screen reader user cannot tell them
   * apart in a list of controls.
   */
  label: string;
  /** More visible actions than this collapse into the overflow menu. Default 3. */
  inlineLimit?: number;
  className?: string;
}

function toneClass(action: RowAction): string {
  return action.tone === "danger" ? "text-negative hover:bg-negative-soft" : "text-ink-soft hover:bg-ground";
}

/**
 * A disabled-looking action that is still focusable.
 *
 * `aria-disabled` rather than `disabled`: a `disabled` button is skipped by the keyboard and its
 * tooltip is never read, so the reason it cannot be used is unreachable by exactly the people
 * most likely to need it. This one takes focus, announces its reason, and does nothing.
 */
function actionProps(action: RowAction, reasonId: string | undefined) {
  if (action.disabled !== true) {
    return { onClick: action.onSelect };
  }
  return {
    "aria-disabled": true as const,
    ...(reasonId !== undefined ? { "aria-describedby": reasonId } : {}),
    ...(action.disabledReason !== undefined ? { title: action.disabledReason } : {}),
    onClick: () => undefined,
  };
}

/**
 * The actions cell at the end of a table row.
 *
 * Real `<button>`s, always — a row whose only affordance is a click on the `<tr>` is a row a
 * keyboard cannot use, and an icon with a `role="button"` and a keydown handler is a bug that
 * took effort. Up to `inlineLimit` actions sit in the row; beyond that they collapse behind one
 * "More actions" button, because five buttons per row in a fifty-row table is a wall.
 *
 * The menu is a real menu: Down/Up move through it, Home/End jump, Escape closes it and returns
 * focus to the trigger, and a click anywhere else closes it. Choosing an item closes it first,
 * so a dialog opening behind it gets focus cleanly.
 */
export function RowActions({ actions, label, inlineLimit = 3, className }: RowActionsProps) {
  const visible = actions.filter((action) => action.hidden !== true);
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const base = useId();

  const overflowing = visible.length > inlineLimit;
  const inline = overflowing ? visible.slice(0, inlineLimit - 1) : visible;
  const overflow = overflowing ? visible.slice(inlineLimit - 1) : [];

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>("[role='menuitem']")?.focus();

    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) === true) return;
      if (triggerRef.current?.contains(target) === true) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  function onMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>("[role='menuitem']") ?? [],
    );
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === "Tab") {
      close(false);
      return;
    }
    if (items.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      items[(index + 1) % items.length]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      items[(index - 1 + items.length) % items.length]?.focus();
    } else if (event.key === "Home") {
      event.preventDefault();
      items[0]?.focus();
    } else if (event.key === "End") {
      event.preventDefault();
      items[items.length - 1]?.focus();
    }
  }

  if (visible.length === 0) return null;

  return (
    <div
      className={cn("flex items-center justify-end gap-1", className)}
      // These sit inside a `DataTable` row that may itself be clickable. Without this, Delete
      // opens the row it was about to delete on the way to the confirmation.
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {inline.map((action, index) => {
        const reasonId = action.disabled === true ? `${base}-reason-${index}` : undefined;
        return (
          <span key={action.label} className="contents">
            <button
              type="button"
              aria-label={`${action.label} ${label}`}
              className={cn(
                "rounded-full px-3 py-1.5 text-sm font-semibold transition-colors",
                toneClass(action),
                action.disabled === true && "text-faint hover:bg-transparent cursor-not-allowed",
              )}
              {...actionProps(action, reasonId)}
            >
              {action.label}
            </button>
            {reasonId !== undefined && action.disabledReason !== undefined ? (
              <span id={reasonId} className="sr-only">
                {action.disabledReason}
              </span>
            ) : null}
          </span>
        );
      })}

      {overflow.length > 0 ? (
        <div className="relative">
          <button
            ref={triggerRef}
            type="button"
            aria-label={`More actions for ${label}`}
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen((previous) => !previous)}
            className="grid size-8 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
          >
            <MoreHorizontalIcon className="size-[18px]" />
          </button>
          {open ? (
            <div
              ref={menuRef}
              role="menu"
              aria-label={`More actions for ${label}`}
              onKeyDown={onMenuKeyDown}
              className={cn(
                "absolute right-0 top-full z-10 mt-1 flex min-w-44 flex-col",
                "rounded-tile border border-line bg-surface py-1 shadow-float",
              )}
            >
              {overflow.map((action, index) => {
                const reasonId = action.disabled === true ? `${base}-menu-reason-${index}` : undefined;
                const props = actionProps(action, reasonId);
                return (
                  <button
                    key={action.label}
                    type="button"
                    role="menuitem"
                    className={cn(
                      "px-4 py-2 text-left text-sm font-medium transition-colors",
                      toneClass(action),
                      action.disabled === true && "text-faint hover:bg-transparent cursor-not-allowed",
                    )}
                    {...props}
                    onClick={() => {
                      if (action.disabled === true) return;
                      close(false);
                      action.onSelect();
                    }}
                  >
                    {action.label}
                    {reasonId !== undefined && action.disabledReason !== undefined ? (
                      <span id={reasonId} className="sr-only">
                        {" "}
                        {action.disabledReason}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
