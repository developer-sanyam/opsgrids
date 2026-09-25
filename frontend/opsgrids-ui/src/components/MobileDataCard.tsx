import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export interface MobileDataField {
  key: string;
  label: ReactNode;
  value: ReactNode;
}

export interface MobileCardSelection {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** The tick box's name: "Select Acme Ltd". */
  label: string;
}

/**
 * What `DataTable` hands a custom `mobileCard` renderer, to spread onto its `MobileDataCard` so
 * the card opens, marks and selects exactly as the table row it stands in for.
 */
export interface MobileCardWiring {
  onOpen?: (() => void) | undefined;
  current?: boolean | undefined;
  selection?: MobileCardSelection | undefined;
}

interface MobileDataCardProps extends MobileCardWiring {
  /** The record's name — also the name of the card's open control. */
  title: ReactNode;
  /** One quiet line under the title: a domain, an email, a reference. */
  subtitle?: ReactNode | undefined;
  /** An avatar or a logo, before the title. */
  leading?: ReactNode | undefined;
  /** Top right: the status badge or two that decide what the user does next. */
  status?: ReactNode | undefined;
  /** The figures worth seeing without opening it, as label and value. Two to four, not every column. */
  fields?: MobileDataField[] | undefined;
  /** At the foot: `RowActions`, a View button. */
  actions?: ReactNode | undefined;
  className?: string | undefined;
}

/**
 * One record as a card, for a phone — where a table is a sideways scroll nobody can use (brief
 * §7: "tables become cards").
 *
 * **The whole card opens the record, and it is a real button.** An empty `<button>` covers the
 * card, named by the title, so a tap anywhere opens it and a keyboard reaches it in one Tab. The
 * card's own content sits above it but lets pointers through, except for its controls — the tick
 * box, the row actions — which stay controls of their own. That is the accessible form of a
 * clickable card: no `div` pretending to be a button, and no button wrapped around other buttons.
 */
export function MobileDataCard({
  title,
  subtitle,
  leading,
  status,
  fields,
  actions,
  onOpen,
  current,
  selection,
  className,
}: MobileDataCardProps) {
  const base = useId();
  const titleId = `${base}-title`;

  return (
    <div
      className={cn(
        "relative flex flex-col gap-3 rounded-card bg-surface p-4 shadow-card",
        (current === true || selection?.checked === true) && "ring-2 ring-accent",
        className,
      )}
    >
      {onOpen ? (
        <button
          type="button"
          aria-labelledby={titleId}
          {...(current !== undefined ? { "aria-pressed": current } : {})}
          onClick={onOpen}
          className="absolute inset-0 rounded-card transition-colors hover:bg-ground/60"
        />
      ) : null}
      <div
        className={cn(
          "relative flex flex-col gap-3",
          // Pointers pass through the content to the open control beneath — except for the
          // content's own controls, which must stay operable.
          onOpen && "pointer-events-none [&_a]:pointer-events-auto [&_button]:pointer-events-auto [&_input]:pointer-events-auto [&_select]:pointer-events-auto",
        )}
      >
        <div className="flex items-start gap-3">
          {selection ? (
            <input
              type="checkbox"
              checked={selection.checked}
              onChange={(event) => selection.onChange(event.target.checked)}
              aria-label={selection.label}
              // 24px: the smallest target WCAG 2.2 allows when it sits on another control (the card).
              className="size-6 shrink-0 cursor-pointer accent-accent"
            />
          ) : null}
          {leading ? <div className="shrink-0">{leading}</div> : null}
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div id={titleId} className="min-w-0 break-words text-sm font-semibold text-ink">
              {title}
            </div>
            {subtitle ? <div className="min-w-0 break-words text-xs text-muted">{subtitle}</div> : null}
          </div>
          {/* At most half the row, wrapping inside it, so a long name keeps room to wrap by word. */}
          {status ? <div className="flex max-w-1/2 shrink-0 flex-wrap justify-end gap-1.5">{status}</div> : null}
        </div>

        {fields && fields.length > 0 ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
            {fields.map((field) => (
              <div key={field.key} className="flex min-w-0 flex-col gap-0.5">
                <dt className="text-xs font-medium text-muted">{field.label}</dt>
                <dd className="tnum min-w-0 break-words text-sm text-ink">{field.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {actions ? (
          <div className="flex items-center justify-end gap-2 border-t border-line pt-3">{actions}</div>
        ) : null}
      </div>
    </div>
  );
}
