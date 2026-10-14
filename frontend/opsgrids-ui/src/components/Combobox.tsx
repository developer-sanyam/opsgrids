import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { cn } from "../lib/cn";
import { CloseIcon, SearchIcon } from "../icons";
import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

export interface ComboboxOption {
  id: string;
  label: string;
  /** The line that identifies rather than describes — an employee number. */
  sublabel?: string | undefined;
}

type Phase = "idle" | "short" | "loading" | "ready" | "error";

interface ComboboxProps {
  label: string;
  /** The current selection. Rendered as a removable chip, not as text in the input. */
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  /**
   * Fetches candidates for a query. Already-capped results are expected — the cap is the
   * server's, and `capHint` says so rather than truncating silently.
   */
  loadOptions: (query: string, signal: AbortSignal) => Promise<ComboboxOption[]>;
  helper?: string | undefined;
  error?: string | undefined;
  /**
   * Keep the label in the accessibility tree but off the screen — for a dense row whose column
   * heading already says what the control is. Default false.
   */
  hideLabel?: boolean | undefined;
  placeholder?: string | undefined;
  /** Below this many characters nothing is requested: a 422 for a state the UI can prevent is a bug. */
  minQueryLength?: number | undefined;
  /** The server's result cap. When exactly this many come back, the footer says so. */
  cap?: number | undefined;
  /**
   * How long a pause in typing counts as "done", in ms. Defaults to a real debounce window; tests
   * lower it so a keystroke burst still coalesces into one request without spending the window's
   * duration in real wall-clock time.
   */
  debounceMs?: number | undefined;
  disabled?: boolean | undefined;
  /** Copy for each pre-result state. Defaults are generic; a screen names its own nouns. */
  copy?: Partial<ComboboxCopy> | undefined;
  className?: string | undefined;
}

export interface ComboboxCopy {
  idle: string;
  short: string;
  loading: string;
  noMatches: (query: string) => string;
  noMatchesHint: string;
  error: string;
  retry: string;
  capHint: (cap: number) => string;
  results: (count: number) => string;
  remove: (label: string) => string;
}

const DEFAULT_COPY: ComboboxCopy = {
  idle: "Start typing a name.",
  short: "Keep typing — at least two letters.",
  loading: "Searching…",
  noMatches: (query) => `No one matches “${query}”.`,
  noMatchesHint: "Check the spelling, or try their employee number.",
  error: "Couldn't load results.",
  retry: "Try again",
  capHint: (cap) => `Showing the first ${cap}. Keep typing to narrow it down.`,
  results: (count) => (count === 1 ? "1 result" : `${count} results`),
  remove: (label) => `Remove ${label}`,
};

const DEBOUNCE_MS = 200;

/**
 * An async search combobox — the delegate picker, and anything after it that looks up a person.
 *
 * The keyboard contract is the whole component: Up/Down move the active option, Home/End jump,
 * Enter selects, Escape closes and leaves the typed text exactly as typed, and the active option
 * is named by `aria-activedescendant` so focus never leaves the input. A picker that can only be
 * driven with a mouse is a defect, not a limitation.
 *
 * It never filters client-side. The endpoint decides who is a candidate — excluding the caller,
 * excluding people who have left — and re-deciding that in the browser is how the two disagree.
 */
export function Combobox({
  label,
  value,
  onChange,
  loadOptions,
  helper,
  error,
  hideLabel = false,
  placeholder,
  minQueryLength = 2,
  cap = 20,
  debounceMs = DEBOUNCE_MS,
  disabled = false,
  copy,
  className,
}: ComboboxProps) {
  const text = { ...DEFAULT_COPY, ...copy };
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<ComboboxOption[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [attempt, setAttempt] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  // Held in a ref so an inline `loadOptions={(q) => …}` — which every call site will write —
  // does not re-fire the search on every render.
  const loadRef = useRef(loadOptions);
  loadRef.current = loadOptions;
  const base = useId();
  const listboxId = `${base}-listbox`;
  const statusId = `${base}-status`;
  const optionId = (index: number) => `${base}-option-${index}`;

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      setPhase("idle");
      setOptions([]);
      return;
    }
    if (trimmed.length < minQueryLength) {
      setPhase("short");
      setOptions([]);
      return;
    }
    const controller = new AbortController();
    setPhase("loading");
    const timer = setTimeout(() => {
      loadRef
        .current(trimmed, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          setOptions(result);
          setActiveIndex(result.length > 0 ? 0 : -1);
          setPhase("ready");
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setOptions([]);
          setPhase("error");
        });
    }, debounceMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, minQueryLength, attempt, debounceMs]);

  const select = useCallback(
    (option: ComboboxOption) => {
      onChange(option);
      setQuery("");
      setOptions([]);
      setPhase("idle");
      setOpen(false);
      setActiveIndex(-1);
    },
    [onChange],
  );

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      // Closes the listbox and leaves the typed text alone — the input was never a preview of
      // the active option, so there is nothing to restore.
      if (open) {
        event.preventDefault();
        setOpen(false);
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) setOpen(true);
      if (options.length === 0) return;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) => {
        const next = index + delta;
        if (next < 0) return options.length - 1;
        if (next >= options.length) return 0;
        return next;
      });
      return;
    }
    if (event.key === "Home" && options.length > 0) {
      event.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (event.key === "End" && options.length > 0) {
      event.preventDefault();
      setActiveIndex(options.length - 1);
      return;
    }
    if (event.key === "Enter") {
      const option = options[activeIndex];
      if (open && option) {
        event.preventDefault();
        select(option);
      }
    }
  }

  /**
   * Retrying moves the phase away from `"error"`, which unmounts this button — and with it,
   * whatever DOM node keyboard focus was resting on. Without putting focus back on the input
   * explicitly, activating "Try again" from the keyboard drops focus to `<body>`.
   */
  function retry() {
    setAttempt((n) => n + 1);
    inputRef.current?.focus();
  }

  const listOpen = open && !disabled;

  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      hideLabel={hideLabel}
      {...(className !== undefined ? { className } : {})}
    >
      {({ id, describedBy, invalid }) => (
        <div className="flex flex-col gap-2">
          {value ? (
            <span className="inline-flex w-fit items-center gap-2 rounded-chip bg-accent-soft py-1 pl-3 pr-1 text-sm font-semibold text-accent-ink">
              <span className="truncate">{value.label}</span>
              {value.sublabel ? (
                <span className="tnum text-xs font-medium opacity-80">{value.sublabel}</span>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  inputRef.current?.focus();
                }}
                aria-label={text.remove(value.label)}
                className="hit-44 relative grid size-6 place-items-center rounded-chip transition-colors hover:bg-surface/60"
              >
                <CloseIcon className="size-4" />
              </button>
            </span>
          ) : null}

          <div className="relative">
            <div
              className={cn(
                fieldSkin(invalid),
                "flex h-11 items-center gap-2",
                // The input inside takes focus, not this box, so the ring is drawn on
                // focus-within — the skin's own `focus-visible` ring never fires on a div.
                "focus-within:outline-2 focus-within:outline-focus focus-within:outline-offset-2",
                disabled && "opacity-60",
              )}
            >
              <SearchIcon className="size-4.5 shrink-0 text-faint" aria-hidden />
              <input
                id={id}
                ref={inputRef}
                type="text"
                role="combobox"
                autoComplete="off"
                aria-expanded={listOpen}
                aria-controls={listboxId}
                aria-autocomplete="list"
                aria-activedescendant={
                  listOpen && activeIndex >= 0 ? optionId(activeIndex) : undefined
                }
                aria-describedby={[describedBy, statusId].filter(Boolean).join(" ")}
                aria-invalid={invalid || undefined}
                disabled={disabled}
                placeholder={placeholder}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onKeyDown={onKeyDown}
                className="-my-px min-w-0 flex-1 self-stretch bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
              />
            </div>

            {listOpen ? (
              <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-tile border border-line bg-surface shadow-float">
                <ul id={listboxId} role="listbox" aria-label={label} className="max-h-64 overflow-y-auto">
                  {options.map((option, index) => (
                    <li
                      key={option.id}
                      id={optionId(index)}
                      role="option"
                      aria-selected={index === activeIndex}
                      onMouseDown={(event) => {
                        // Before blur, so the click does not close the list first.
                        event.preventDefault();
                        select(option);
                      }}
                      onMouseEnter={() => setActiveIndex(index)}
                      className={cn(
                        "cursor-pointer px-3 py-2",
                        index === activeIndex ? "bg-accent-soft" : "hover:bg-ground",
                      )}
                    >
                      <span className="block text-sm font-semibold text-ink">{option.label}</span>
                      {option.sublabel ? (
                        <span className="block text-xs tnum text-muted">{option.sublabel}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {phase === "ready" && options.length >= cap ? (
                  <p className="border-t border-line px-3 py-2 text-xs text-muted">
                    {text.capHint(cap)}
                  </p>
                ) : null}
                {phase === "error" ? (
                  <div className="flex items-center justify-between gap-3 px-3 py-2">
                    <span className="text-sm text-muted">{text.error}</span>
                    <button
                      type="button"
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={retry}
                      className="rounded-full text-sm font-semibold text-accent-ink hover:text-accent"
                    >
                      {text.retry}
                    </button>
                  </div>
                ) : null}
                {phase === "ready" && options.length === 0 ? (
                  <div className="px-3 py-3">
                    <p className="text-sm font-semibold text-ink">{text.noMatches(query.trim())}</p>
                    <p className="text-xs text-muted">{text.noMatchesHint}</p>
                  </div>
                ) : null}
                {phase === "idle" ? <p className="px-3 py-3 text-sm text-muted">{text.idle}</p> : null}
                {phase === "short" ? (
                  <p className="px-3 py-3 text-sm text-muted">{text.short}</p>
                ) : null}
                {phase === "loading" ? (
                  <p className="px-3 py-3 text-sm text-muted">{text.loading}</p>
                ) : null}
              </div>
            ) : null}
          </div>

          {/*
            The picker's own polite region: how many matched, said once per result set, or that
            the lookup failed and a retry is on offer — a screen-reader user driving this by
            keyboard never sees the error row appear, so silence here would mean never being
            told the search failed at all.
          */}
          <span id={statusId} aria-live="polite" className="sr-only">
            {phase === "ready"
              ? text.results(options.length)
              : phase === "error"
                ? `${text.error} ${text.retry}.`
                : ""}
          </span>
        </div>
      )}
    </FormField>
  );
}
