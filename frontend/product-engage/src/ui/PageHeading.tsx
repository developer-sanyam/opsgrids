import type { ReactNode, Ref } from "react";

/**
 * A page's title block — eyebrow, `h1`, description, actions — with an `h1` that focus can be sent
 * to. It mirrors `PageHeader` from `@opsgrids/ui` and exists for one reason: `PageHeader` renders
 * the `h1` itself and offers no way to reach it.
 *
 * When the control someone just used is replaced — a Send button on a draft that has become a
 * report, the row of a campaign that was deleted — the browser drops focus to the document, and a
 * keyboard or screen-reader user starts again from the top of the page with no idea what happened.
 * Sending focus to the page's own `h1` says where they are and reads "heading level 1, <title>";
 * focusing a span inside it reads only the words.
 *
 * `tabIndex={-1}` makes the heading focusable by script and not part of the tab order, and it is
 * the one place this product removes the focus outline: a heading is not a control, and a ring
 * around it after every action would say "press me".
 *
 * **This is a stopgap, not a second design.** The long-term change is a `titleRef` on `PageHeader`
 * in `@opsgrids/ui`, after which this file is deleted and every screen goes back to `PageHeader`.
 * The type steps are the design system's own scale (`text-3xl`, `text-4xl`), not its arbitrary
 * `2.1rem`, so nothing here is a value the token lint would refuse.
 */
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
  ref,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: string;
  actions?: ReactNode;
  ref?: Ref<HTMLHeadingElement>;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1">
        {eyebrow ? <span className="text-sm font-medium text-muted">{eyebrow}</span> : null}
        <h1
          ref={ref}
          tabIndex={-1}
          className="display text-3xl font-extrabold leading-tight outline-none sm:text-4xl"
        >
          {title}
        </h1>
        {description ? <p className="max-w-2xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
