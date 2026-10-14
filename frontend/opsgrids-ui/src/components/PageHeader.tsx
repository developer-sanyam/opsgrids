import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { SM_QUERY, useMediaQuery } from "../lib/useMediaQuery";
import { Button } from "./Button";
import { Menu } from "./Menu";
import type { MenuItem } from "./Menu";

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
  /** The screen's primary action or two. Always shown; wraps under the title when there is no room. */
  actions?: ReactNode | undefined;
  /**
   * Everything that sits beside the primary action but is not it — Export, Import, Archive. From
   * 640px they are secondary buttons in the row; below it they collapse into one "More actions"
   * menu, so a phone's header keeps the title, the primary action and a single extra control
   * instead of a stack of wrapped labels.
   */
  secondaryActions?: MenuItem[] | undefined;
}

/**
 * A screen's title block: a quiet eyebrow, the page heading (the `page` type step — 28px, 22px on
 * a phone), an optional sentence of orientation, and right-aligned actions that wrap under the
 * title when there is no room beside it.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  descriptionClassName,
  meta,
  actions,
  secondaryActions,
}: PageHeaderProps) {
  const roomy = useMediaQuery(SM_QUERY, true);
  const secondary = (secondaryActions ?? []).filter((item) => item.hidden !== true);
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
      {actions || secondary.length > 0 ? (
        <div className="flex max-w-full flex-wrap items-center gap-2">
          {roomy
            ? secondary.map((item) => (
                <Button
                  key={item.label}
                  variant="secondary"
                  size="sm"
                  leadingIcon={item.icon}
                  disabled={item.disabled === true}
                  title={item.disabled === true ? item.disabledReason : undefined}
                  onClick={item.onSelect}
                >
                  {item.label}
                </Button>
              ))
            : secondary.length > 0 ? <Menu label="More actions" trigger="icon" align="end" items={secondary} /> : null}
          {actions}
        </div>
      ) : null}
    </header>
  );
}
