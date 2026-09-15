import type { InputHTMLAttributes } from "react";
import { useId } from "react";

import { cn } from "@/lib/cn";
import { SearchIcon } from "./icons";

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  containerClassName?: string;
}

/** A pill search field with a leading icon and an accessible, visually-hidden label. */
export function SearchInput({ label, containerClassName, className, id, ...props }: SearchInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-full bg-ground px-4 h-11",
        "focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2",
        containerClassName,
      )}
    >
      <label htmlFor={inputId} className="sr-only">
        {label}
      </label>
      <SearchIcon className="size-[18px] text-faint" />
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
