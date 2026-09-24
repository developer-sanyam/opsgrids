import type { InputHTMLAttributes } from "react";
import { useId } from "react";

import { cn } from "../lib/cn";
import { SearchIcon } from "../icons";

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  containerClassName?: string | undefined;
  /**
   * The colour of the focus-within ring. `ink` is the default quiet treatment; `accent` matches
   * the global `:focus-visible` ring, for screens where search is the primary control.
   */
  focusRing?: "ink" | "accent" | undefined;
}

/** A search field with a leading icon and an accessible, visually-hidden label, on the field skin. */
export function SearchInput({
  label,
  containerClassName,
  className,
  focusRing = "ink",
  id,
  ...props
}: SearchInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div
      className={cn(
        "flex h-11 items-center gap-2 rounded-tile border border-field-edge bg-surface px-3",
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
          "min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint",
          "focus:outline-none",
          className,
        )}
        {...props}
      />
    </div>
  );
}
