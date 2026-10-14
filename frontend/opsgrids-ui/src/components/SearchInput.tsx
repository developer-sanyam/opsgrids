import type { ChangeEvent, InputHTMLAttributes } from "react";
import { useEffect, useId, useRef, useState } from "react";

import { cn } from "../lib/cn";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { SearchIcon } from "../icons";

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  containerClassName?: string | undefined;
  /**
   * The colour of the focus-within ring. `ink` is the default quiet treatment; `accent` matches
   * the global `:focus-visible` ring, for screens where search is the primary control.
   */
  focusRing?: "ink" | "accent" | undefined;
  /**
   * The query, once typing pauses for `debounceMs` — what a list's fetch should key on, so a
   * search request goes out per pause rather than per keystroke. Clearing the box reports `""`
   * at once: an emptied search is a decision, not typing in progress.
   *
   * With this set the field keeps its own draft of what is being typed, and `value`, if given,
   * is the committed query: when it changes from outside — a "Clear filters" button — the draft
   * follows it. `onChange` still fires on every keystroke for anything that wants them.
   */
  onDebouncedChange?: ((value: string) => void) | undefined;
  /** The pause, in milliseconds. Default 300. */
  debounceMs?: number | undefined;
}

/**
 * The debounced half of `SearchInput`: owns the draft and reports it after a pause. Called in
 * both modes (hooks cannot be conditional), but a search box that does not debounce never reads
 * the draft, so it renders exactly what it always did.
 */
function useDebouncedDraft(
  committed: string,
  onDebouncedChange: ((value: string) => void) | undefined,
  debounceMs: number,
) {
  const [draft, setDraft] = useState(committed);
  const reported = useRef(committed);
  const debounced = useDebouncedValue(draft, debounceMs);

  // The committed query moved without us (a Clear button): the box shows it, and it is already
  // known upstream, so it is not reported back.
  useEffect(() => {
    if (committed === reported.current) return;
    reported.current = committed;
    setDraft(committed);
  }, [committed]);

  useEffect(() => {
    if (onDebouncedChange === undefined || debounced === reported.current || debounced !== draft) return;
    reported.current = debounced;
    onDebouncedChange(debounced);
  }, [debounced, draft, onDebouncedChange]);

  function change(next: string) {
    setDraft(next);
    if (next === "" && reported.current !== "" && onDebouncedChange !== undefined) {
      reported.current = "";
      onDebouncedChange("");
    }
  }

  return { draft, change };
}

/** A search field with a leading icon and an accessible, visually-hidden label, on the field skin. */
export function SearchInput({
  label,
  containerClassName,
  className,
  focusRing = "ink",
  id,
  onDebouncedChange,
  debounceMs = 300,
  ...props
}: SearchInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const initial = String(props.value ?? props.defaultValue ?? "");
  const { draft, change } = useDebouncedDraft(
    onDebouncedChange !== undefined && props.value !== undefined ? String(props.value) : initial,
    onDebouncedChange,
    debounceMs,
  );

  // Debouncing, the input is controlled by the draft; otherwise the caller's props pass through
  // untouched, controlled or not, exactly as before this option existed.
  const debouncing = onDebouncedChange !== undefined;
  const { value, defaultValue, onChange, ...rest } = props;
  const valueProps = debouncing
    ? {
        value: draft,
        onChange: (event: ChangeEvent<HTMLInputElement>) => {
          change(event.target.value);
          onChange?.(event);
        },
      }
    : {
        ...(value !== undefined ? { value } : {}),
        ...(defaultValue !== undefined ? { defaultValue } : {}),
        ...(onChange !== undefined ? { onChange } : {}),
      };

  return (
    <div
      className={cn(
        // `min-h-11`, not `h-11`: in a column flex with `flex-1` the basis wins over a height and the field collapsed to its text.
        "flex min-h-11 items-center gap-2 rounded-tile border border-field-edge bg-surface px-3",
        "transition-colors duration-(--duration-quick) hover:border-muted",
        "focus-within:outline focus-within:outline-2 focus-within:outline-offset-2",
        focusRing === "accent" ? "focus-within:outline-focus" : "focus-within:outline-ink",
        containerClassName,
      )}
    >
      <label htmlFor={inputId} className="sr-only">
        {label}
      </label>
      <SearchIcon className="size-4.5 shrink-0 text-faint" />
      <input
        id={inputId}
        type="search"
        className={cn(
          "-my-px min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink placeholder:text-faint",
          "focus:outline-none",
          className,
        )}
        {...rest}
        {...valueProps}
      />
    </div>
  );
}
