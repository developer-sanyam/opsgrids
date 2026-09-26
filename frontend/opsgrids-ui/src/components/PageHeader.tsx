import type { ReactNode } from "react";

import { cn } from "../lib/cn";

interface PageHeaderProps {
  eyebrow?: string | undefined;
  title: ReactNode;
  /** An optional line under the heading, for screens that need a sentence of orientation. */
  description?: string | undefined;
  /**
   * Extra classes on the description paragraph — a dashboard whose title and period control must
   * share one row on a phone (mock-1, mock-2) passes `"hidden sm:block"` to drop the sentence
   * there rather than let it push `actions` onto a line of its own. Omitted, the description
   * shows at every width, exactly as before this existed.
   */
  descriptionClassName?: string | undefined;
  actions?: ReactNode | undefined;
}

/**
 * A screen's title block: a quiet eyebrow, the page heading (the `page` type step — 28px, 22px on
 * a phone), an optional sentence of orientation, and right-aligned actions that wrap under the
 * title when there is no room beside it.
 */
export function PageHeader({ eyebrow, title, description, descriptionClassName, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        {eyebrow ? (
          <span className="text-sm font-medium text-muted">{eyebrow}</span>
        ) : null}
        <h1 className="text-page-sm text-balance sm:text-page">
          {title}
        </h1>
        {description ? <p className={cn("max-w-2xl text-sm text-muted", descriptionClassName)}>{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
