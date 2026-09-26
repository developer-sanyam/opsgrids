import type { ReactNode, Ref } from "react";
import { Link } from "react-router-dom";

import { CheckIcon, cn } from "@opsgrids/ui";

import { isNotActivated } from "@/lib/errors";
import { Card, EngageNotActive, ErrorState } from "@/ui";

/**
 * Pieces shared by the connect wizard and the settings screen.
 *
 * `LinkButton` is a router link drawn as the design system's `Button` — the system has no link
 * variant yet, and a navigation that is a `<button>` with an `onClick` is not a link (no
 * open-in-new-tab, no URL in the status bar). It uses the same tokens `Button` does; when the
 * design system grows a link variant this file's copy of the classes is what gets deleted.
 */

const LINK_BASE =
  "inline-flex h-11 items-center justify-center gap-2 rounded-tile px-4 text-sm font-semibold transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

const LINK_VARIANT = {
  primary: "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong",
  secondary: "border border-field-edge bg-surface text-ink hover:bg-sunken active:bg-sunken",
} as const;

export function LinkButton({
  to,
  variant = "secondary",
  children,
  className,
}: {
  to: string;
  variant?: keyof typeof LINK_VARIANT;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Link to={to} className={cn(LINK_BASE, LINK_VARIANT[variant], className)}>
      {children}
    </Link>
  );
}

/** A read that failed: the not-active state for a 402, otherwise the error with a Try again. */
export function LoadError({
  error,
  title,
  onRetry,
}: {
  error: Error;
  title: string;
  onRetry: () => void;
}) {
  if (isNotActivated(error)) return <EngageNotActive />;
  return (
    <Card className="p-6">
      <ErrorState message={error.message} title={title} onRetry={onRetry} />
    </Card>
  );
}

/**
 * A wizard step's surface. The `h1` is the step's title and the focus target when the step
 * changes: a step change inside one page moves no focus of its own, so without it a screen-reader
 * user stays parked on a button that has just left the page.
 */
export function StepCard({
  title,
  description,
  headingRef,
  size = "md",
  centered = false,
  children,
}: {
  title: string;
  description?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
  size?: "md" | "lg";
  centered?: boolean;
  children: ReactNode;
}) {
  return (
    <Card
      className={cn(
        "mx-auto flex w-full flex-col gap-6 p-6 sm:p-8",
        size === "md" ? "max-w-2xl" : "max-w-3xl",
      )}
    >
      <div className={cn("flex flex-col gap-2", centered && "items-center text-center")}>
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-page-sm text-balance outline-none sm:text-page"
        >
          {title}
        </h1>
        {description !== undefined ? (
          <p className="max-w-xl text-body text-muted">{description}</p>
        ) : null}
      </div>
      {children}
    </Card>
  );
}

const PROGRESS = ["Connect", "Choose number", "Set up", "Done"] as const;

/** Which of the four progress stages a URL step belongs to: the Meta window is still "Connect". */
export type WizardStep = "start" | "meta" | "select" | "setup" | "done";

function stageOf(step: WizardStep): number {
  if (step === "start" || step === "meta") return 0;
  if (step === "select") return 1;
  if (step === "setup") return 2;
  return 3;
}

/** The four-stage progress row from the design. A read-only indicator: the steps are reached by
 *  doing them, not by jumping, so none of it is a control. */
export function ConnectProgress({ step }: { step: WizardStep }) {
  const current = stageOf(step);
  return (
    <ol
      aria-label="Setup progress"
      className="mx-auto grid w-full max-w-3xl list-none grid-cols-4 gap-2 p-0"
    >
      {PROGRESS.map((label, index) => {
        const done = index < current;
        const now = index === current;
        return (
          <li
            key={label}
            aria-current={now ? "step" : undefined}
            className="flex flex-col items-center gap-2 text-center"
          >
            <span
              aria-hidden
              className={cn(
                "grid size-8 place-items-center rounded-full text-sm font-bold tnum",
                now && "bg-accent text-on-accent",
                done && "bg-accent-soft text-accent-ink",
                !now && !done && "border border-line bg-surface text-muted",
              )}
            >
              {done ? <CheckIcon className="size-4" strokeWidth={2.4} /> : index + 1}
            </span>
            <span className={cn("text-body-sm font-semibold", now ? "text-accent-ink" : "text-ink-soft")}>
              {label}
              {done ? <span className="sr-only">, completed</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
