import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card } from "@/ui";

interface ComingSoonPageProps {
  icon?: ReactNode;
  title: string;
  description: string;
  /** Defaults to "Coming soon" — the 404 route passes "Not found" instead, since a page that
   *  doesn't exist isn't a feature that's on its way. */
  eyebrow?: string;
}

/** A tasteful placeholder for surfaces not built yet (Leave, Payroll) and 404. */
export function ComingSoonPage({ icon, title, description, eyebrow = "Coming soon" }: ComingSoonPageProps) {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="w-full max-w-lg p-10 text-center flex flex-col items-center gap-5">
        {icon ? (
          <span className="grid size-16 place-items-center rounded-full bg-accent-soft text-accent-ink">
            {icon}
          </span>
        ) : null}
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">
            {eyebrow}
          </span>
          <h1 className="display text-3xl font-extrabold">{title}</h1>
          <p className="text-sm text-muted leading-relaxed max-w-sm mx-auto">
            {description}
          </p>
        </div>
        <Link
          to="/"
          className="rounded-full bg-accent px-5 h-11 inline-flex items-center font-semibold text-white hover:bg-accent-strong transition-colors"
        >
          Back to dashboard
        </Link>
      </Card>
    </div>
  );
}
