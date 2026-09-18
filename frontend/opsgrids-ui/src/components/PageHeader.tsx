import type { ReactNode } from "react";

interface PageHeaderProps {
  eyebrow?: string;
  title: ReactNode;
  /** An optional line under the heading, for screens that need a sentence of orientation. */
  description?: string;
  actions?: ReactNode;
}

/** A screen's title block: a quiet eyebrow, a big tight heading, optional right-aligned actions. */
export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1">
        {eyebrow ? (
          <span className="text-sm font-medium text-muted">{eyebrow}</span>
        ) : null}
        <h1 className="display text-3xl sm:text-[2.1rem] font-extrabold leading-tight">
          {title}
        </h1>
        {description ? <p className="max-w-2xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
