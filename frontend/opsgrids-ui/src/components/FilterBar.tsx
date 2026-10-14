import { useRef, useState } from "react";
import type { ReactNode } from "react";

import { CloseIcon, FilterIcon } from "../icons";
import { cn } from "../lib/cn";
import { TABLET_QUERY, useMediaQuery } from "../lib/useMediaQuery";
import { useScrollFade } from "../lib/useScrollFade";
import { Button } from "./Button";
import { Drawer } from "./Drawer";
import { SearchInput } from "./SearchInput";
import { Select } from "./Select";
import type { SelectOption } from "./Select";

export interface FilterSelect {
  /** Stable key. */
  key: string;
  /** What is filtered: "Plan", "Status". The select's label and its chip's prefix. */
  label: string;
  /** The choices. The unfiltered one is added for you, as `allLabel` with the value `""`. */
  options: SelectOption[];
  /** `""` is "not filtered". */
  value: string;
  onChange: (value: string) => void;
  /** The unfiltered choice as the select shows it: "All plans". */
  allLabel: string;
}

export interface FilterChip {
  key: string;
  /** The criterion in words: "Owner: me", "Closing this month". */
  label: string;
  onRemove: () => void;
}

export interface FilterSearch {
  /** The field's accessible name: "Search accounts". */
  label: string;
  placeholder?: string | undefined;
  /** The committed query. */
  value: string;
  /** Called once typing pauses (and at once when cleared) — key the list's fetch on it. */
  onChange: (value: string) => void;
  debounceMs?: number | undefined;
}

interface FilterBarProps {
  search?: FilterSearch | undefined;
  filters?: FilterSelect[] | undefined;
  /**
   * Criteria that are not one of `filters` — a quick filter, a value carried in from a link. Each
   * active select already gets a chip of its own.
   */
  chips?: FilterChip[] | undefined;
  /** Shown as "Clear all" whenever anything is filtered. Should reset the search as well. */
  onClearAll?: (() => void) | undefined;
  /** The screen's own actions, at the end of the row: Export, Create account. */
  actions?: ReactNode | undefined;
  /** Names the search landmark: "Filter accounts". */
  label?: string | undefined;
  className?: string | undefined;
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <li className="inline-flex h-8 shrink-0 items-center gap-1 rounded-full bg-accent-soft pl-3 pr-1 text-sm font-medium text-accent-ink">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
        className="hit-44 relative grid size-6 place-items-center rounded-full transition-colors hover:bg-surface"
      >
        <CloseIcon className="size-3.5" />
      </button>
    </li>
  );
}

function FilterSelects({ filters, compact }: { filters: FilterSelect[]; compact: boolean }) {
  return filters.map((filter) => (
    <Select
      key={filter.key}
      label={filter.label}
      hideLabel={compact}
      value={filter.value}
      onChange={(event) => filter.onChange(event.target.value)}
      options={[{ value: "", label: filter.allLabel }, ...filter.options]}
      containerClassName={compact ? "min-w-40" : undefined}
    />
  ));
}

/**
 * The row above a list: search, the filters, the list's own actions — and, beneath it, a chip for
 * every criterion in force with a way to remove each one and all of them (brief §7).
 *
 * **One set of filters, two layouts.** From 768px the selects sit in the row, labelled for a
 * screen reader and titled by their "All …" choice for the eye. On a phone the row keeps only the
 * search and a Filters button that says how many are active; the selects open in a bottom sheet
 * with visible labels and apply as they change, so "Show results" only closes it.
 *
 * Chips are derived: an active select is a chip, so the two can never disagree. Removing a chip
 * sets its filter back to `""`.
 */
export function FilterBar({
  search,
  filters = [],
  chips = [],
  onClearAll,
  actions,
  label = "Filters",
  className,
}: FilterBarProps) {
  const wide = useMediaQuery(TABLET_QUERY, true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const chipsRef = useRef<HTMLUListElement>(null);

  const active: FilterChip[] = [
    ...filters
      .filter((filter) => filter.value !== "")
      .map((filter) => ({
        key: `filter-${filter.key}`,
        label: `${filter.label}: ${filter.options.find((option) => option.value === filter.value)?.label ?? filter.value}`,
        onRemove: () => filter.onChange(""),
      })),
    ...chips,
  ];
  const activeFilters = filters.filter((filter) => filter.value !== "").length;
  const anything = active.length > 0 || (search !== undefined && search.value !== "");
  const chipFade = useScrollFade(chipsRef, active.length);

  return (
    <div role="search" aria-label={label} className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap items-center gap-2">
        {search ? (
          <SearchInput
            label={search.label}
            placeholder={search.placeholder}
            value={search.value}
            onDebouncedChange={search.onChange}
            debounceMs={search.debounceMs}
            containerClassName="min-w-0 flex-1 md:max-w-sm"
          />
        ) : null}
        {filters.length > 0 && wide ? <FilterSelects filters={filters} compact /> : null}
        {filters.length > 0 && !wide ? (
          <Button
            variant="outline"
            leadingIcon={<FilterIcon className="size-4" />}
            onClick={() => setSheetOpen(true)}
          >
            Filters
            {activeFilters > 0 ? (
              <>
                {/* The space is for the accessible name ("Filters 2 active"); the flex gap draws it. */}{" "}
                <span className="tnum rounded-full bg-accent-soft px-2 text-xs text-accent-ink">{activeFilters}</span>
                <span className="sr-only"> active</span>
              </>
            ) : null}
          </Button>
        ) : null}
        {actions ? <div className="flex items-center gap-2 md:ml-auto">{actions}</div> : null}
      </div>

      {active.length > 0 || (anything && onClearAll) ? (
        <div className="flex flex-wrap items-center gap-2">
          {active.length > 0 ? (
            <ul
              ref={chipsRef}
              data-fade={chipFade}
              aria-label="Active filters"
              className="scroll-fade flex max-w-full items-center gap-2 overflow-x-auto md:flex-wrap md:overflow-visible"
            >
              {active.map((chip) => (
                <Chip key={chip.key} label={chip.label} onRemove={chip.onRemove} />
              ))}
            </ul>
          ) : null}
          {anything && onClearAll ? (
            <Button variant="ghost" size="sm" onClick={onClearAll}>
              Clear all
            </Button>
          ) : null}
        </div>
      ) : null}

      {!wide ? (
        <Drawer
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          title="Filters"
          dismissOnBackdrop
          footer={
            <>
              {onClearAll ? (
                <Button variant="ghost" onClick={onClearAll} disabled={!anything}>
                  Clear all
                </Button>
              ) : null}
              <Button variant="dark" onClick={() => setSheetOpen(false)}>
                Show results
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-4">
            <FilterSelects filters={filters} compact={false} />
          </div>
        </Drawer>
      ) : null}
    </div>
  );
}
