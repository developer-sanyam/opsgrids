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
  /**
   * A quiet line of status under the description — "Showing 15 Sep – 14 Oct" beside a period
   * picker. It lives with the title, never inside `actions`: a caption stacked under one control
   * makes that control's cell taller than its neighbours, and a row of centred controls then
   * drifts out of line with it.
   */
  meta?: ReactNode | undefined;
  actions?: ReactNode | undefined;
}

/**
 * A screen's title block: a quiet eyebrow, the page heading (the `page` type step — 28px, 22px on
 * a phone), an optional sentence of orientation, and right-aligned actions that wrap under the
 * title when there is no room beside it.
 */
export function PageHeader({ eyebrow, title, description, descriptionClassName, meta, actions }: PageHeaderProps) {
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
        {meta ? <div className="text-xs text-muted">{meta}</div> : null}
      </div>
      {/*
        Every direct child is one control (or one control row) — they share a centre line, so a
        sm button beside a md select still reads as one row. A caption does not belong in here:
        put it in `meta`.
      */}
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
