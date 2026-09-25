import type { ReactNode } from "react";

interface PageHeaderProps {
  eyebrow?: string | undefined;
  title: ReactNode;
  /** An optional line under the heading, for screens that need a sentence of orientation. */
  description?: string | undefined;
  actions?: ReactNode | undefined;
}

/**
 * A screen's title block: a quiet eyebrow, the page heading (the `page` type step — 28px, 22px on
 * a phone), an optional sentence of orientation, and right-aligned actions that wrap under the
 * title when there is no room beside it.
 */
export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1">
        {eyebrow ? (
          <span className="text-sm font-medium text-muted">{eyebrow}</span>
        ) : null}
        <h1 className="text-page-sm text-balance sm:text-page">
          {title}
        </h1>
        {description ? <p className="max-w-2xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
