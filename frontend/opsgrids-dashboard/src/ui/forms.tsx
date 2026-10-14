/**
 * The pieces the four unauthenticated screens share — one card centred on the ground, a labelled
 * field, one inline error — so sign-up, sign-in, forgot and reset read as the same door. The field
 * and the error are `@opsgrids/ui`'s `FormField` and `InlineAlert`; nothing here invents a colour.
 */
import { Children, cloneElement, isValidElement, useEffect, useRef } from "react";
import type { ReactElement, ReactNode } from "react";

import { FormField, InlineAlert } from "@opsgrids/ui";

import { LogoMark } from "./LogoMark";

export function AuthCard({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: ReactNode;
  children: ReactNode;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousTitle = useRef(title);

  // Moving from one step to the next (password, then code, then workspace) puts focus on the new
  // heading, so a keyboard or screen-reader user lands where the content changed.
  useEffect(() => {
    if (previousTitle.current === title) return;
    previousTitle.current = title;
    headingRef.current?.focus();
  }, [title]);

  return (
    <div className="min-h-dvh bg-ground">
      <main className="grid min-h-dvh place-items-center px-4 py-12">
        <div className="rise w-full max-w-md">
          <div className="mb-6 flex items-center justify-center gap-3">
            <LogoMark className="size-9" />
            <span className="text-section font-bold tracking-tight">OpsGrids</span>
          </div>
          <div className="rounded-card bg-surface p-6 shadow-float sm:p-8">
            <header className="mb-6 flex flex-col gap-2">
              <h1 ref={headingRef} tabIndex={-1} className="display text-page-sm text-balance focus:outline-none">
                {title}
              </h1>
              {intro ? <p className="text-sm text-muted">{intro}</p> : null}
            </header>
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}

/**
 * A labelled control. The label, the optional hint and the control are tied together by
 * `FormField`; `id` is the one the control carries and `className={inputClass}` is its skin.
 */
export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <FormField label={label} controlId={id} {...(hint !== undefined ? { helper: hint } : {})}>
      {({ describedBy }) =>
        isValidElement(children)
          ? cloneElement(children as ReactElement<{ "aria-describedby"?: string | undefined }>, {
              "aria-describedby": describedBy,
            })
          : children
      }
    </FormField>
  );
}

/**
 * The one error a form raises, announced when it appears. A plain sentence is the alert's title;
 * a sentence followed by a link ("…Request a link") keeps the link beneath it.
 */
export function InlineError({ children }: { children: ReactNode }) {
  const [first, ...rest] = Children.toArray(children);
  if (typeof first === "string") {
    return (
      <InlineAlert tone="danger" title={first.trim()}>
        {rest.length > 0 ? rest : undefined}
      </InlineAlert>
    );
  }
  return <InlineAlert tone="danger" title="Something went wrong.">{children}</InlineAlert>;
}
