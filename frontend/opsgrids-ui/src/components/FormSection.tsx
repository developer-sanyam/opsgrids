import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

interface FormSectionProps {
  /** "Basic information", "Pricing & stock". */
  title: string;
  /** One line on what the section is for: "Add the main details about your product." */
  description?: ReactNode | undefined;
  /**
   * Columns from 768px up; always one below it (brief §8–9: desktop two where appropriate,
   * mobile one). `3` reaches three columns only at 1280px, keeping two on a tablet. A field that
   * needs the full row — a description, an upload — takes `containerClassName="col-span-full"`.
   */
  columns?: 1 | 2 | 3 | undefined;
  /** `2` for a section of a page; `3` for a section inside a dialog or a stepper step. */
  headingLevel?: 2 | 3 | undefined;
  children: ReactNode;
  className?: string | undefined;
}

const GRID = {
  1: "grid-cols-1",
  2: "grid-cols-1 md:grid-cols-2",
  3: "grid-cols-1 md:grid-cols-2 xl:grid-cols-3",
};

/**
 * A titled group of fields in a responsive grid — the `grid gap-4 sm:grid-cols-2` block every form
 * wrote by hand, with a different column count per app. It is a `<section>` named by its heading,
 * so a screen reader's landmark list reads the form's structure.
 *
 * **Required fields inside are marked**, the same as anywhere else. A `TextField`, `TextArea` or
 * `Select` given `required` shows a red asterisk after its label; being inside this section
 * changes nothing about that. The asterisk is decoration for the eye; the control's own
 * `required` is what a screen reader announces, as "required", once.
 */
export function FormSection({
  title,
  description,
  columns = 2,
  headingLevel = 2,
  children,
  className,
}: FormSectionProps) {
  const base = useId();
  const headingId = `${base}-title`;
  const Heading = headingLevel === 2 ? "h2" : "h3";

  return (
    <section aria-labelledby={headingId} className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-col gap-1">
        <Heading id={headingId} className="text-base font-bold text-ink">
          {title}
        </Heading>
        {description ? <p className="text-sm text-muted">{description}</p> : null}
      </div>
      <div className={cn("grid gap-4", GRID[columns])}>{children}</div>
    </section>
  );
}
