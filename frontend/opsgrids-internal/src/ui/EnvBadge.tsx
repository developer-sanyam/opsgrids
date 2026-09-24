import { cn } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/config";

/**
 * A persistent environment marker. While the console runs on fixtures, a labelled amber chip makes
 * that unmistakable so nobody reads sample figures as live revenue. Against the live gateway it
 * flips to a quiet "Live" marker. The words carry the meaning; the colour agrees with them.
 */
export function EnvBadge({ className }: { className?: string | undefined }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
        USE_FIXTURES ? "bg-late-soft text-late" : "bg-present-soft text-present",
        className,
      )}
      title={
        USE_FIXTURES
          ? "Fixtures mode — representative data, not a live read."
          : "Live — reading admin-gateway on the operator audience."
      }
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {USE_FIXTURES ? "Fixtures data" : "Live"}
    </span>
  );
}
