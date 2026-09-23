import type { ReactNode, Ref } from "react";

/**
 * A page's heading text as somewhere focus can be sent.
 *
 * When the control someone just used goes away — a Send button on a draft that has become a
 * report, the row of a campaign that was deleted — the browser drops focus to the document, and a
 * keyboard or screen-reader user starts again from the top of the page with no idea what happened.
 * Sending focus to the page's heading instead says where they are and reads the new title.
 *
 * `tabIndex={-1}` makes it focusable by script and not part of the tab order, and it is the one
 * place this product removes the focus outline: a heading is not a control, and a ring around it
 * after every action would say "press me".
 */
export function PageTitle({ children, ref }: { children: ReactNode; ref?: Ref<HTMLSpanElement> }) {
  return (
    <span ref={ref} tabIndex={-1} className="outline-none">
      {children}
    </span>
  );
}
