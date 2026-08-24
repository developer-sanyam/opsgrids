import type { ReactNode } from "react";

/** A screen's title block: a quiet eyebrow, a big tight heading, optional right-aligned actions. */
export function PageHeader({
  eyebrow,
  title,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1">
        {eyebrow ? (
          <span className="text-sm font-medium text-muted">{eyebrow}</span>
        ) : null}
        <h1 className="display text-3xl sm:text-[2.1rem] font-extrabold leading-tight">
          {title}
        </h1>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
