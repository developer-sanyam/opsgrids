import { USE_FIXTURES } from "@/api/client";
import { cn } from "@/lib/cn";

/**
 * A persistent environment marker. While the console runs on fixtures (the console backend is not
 * wired yet), a labelled amber chip makes that unmistakable so nobody reads sample figures as live
 * revenue. Against the live gateway it flips to a quiet "Live" marker.
 */
export function EnvBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
        USE_FIXTURES ? "bg-late-soft text-late" : "bg-present-soft text-present",
        className,
      )}
      title={
        USE_FIXTURES
          ? "Fixtures mode — representative data. The console backend is not wired yet."
          : "Live — reading admin-gateway on the operator audience."
      }
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {USE_FIXTURES ? "Fixtures data" : "Live"}
    </span>
  );
}
