import { Link } from "react-router-dom";

import { ArrowRightIcon } from "@opsgrids/ui";

/** The way back up from a detail screen, shared by the manufacturing screens. */
export function BackLink({ to, children }: { to: string; children: string }) {
  return (
    <Link
      to={to}
      className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      {children}
    </Link>
  );
}
