import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card, PageHeader } from "@/ui";

/** A named screen that is not built yet. It says what will live here and offers the way back. */
export function ComingSoonPage({
  icon,
  title,
  description,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Storefront" title={title} />
      <Card className="flex flex-col items-start gap-3 p-8">
        {icon ? (
          <span className="grid size-12 place-items-center rounded-tile bg-accent-soft text-accent-ink">
            {icon}
          </span>
        ) : null}
        <p className="max-w-prose text-sm text-muted">{description}</p>
        <Link
          to="/"
          className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
        >
          Back to themes
        </Link>
      </Card>
    </div>
  );
}
