import type { ActivityItem } from "@/api/types";

const DOT_TONE: Record<ActivityItem["tone"], string> = {
  accent: "bg-accent",
  positive: "bg-present",
  info: "bg-info",
  late: "bg-late",
};

/**
 * A timeline of recent people-ops events. Each entry has a tone dot, a sentence naming the actor
 * and target, and a relative timestamp. Sample data until the activity feed is wired.
 */
export function RecentActivity({ items }: { items: ActivityItem[] }) {
  return (
    <ol className="flex flex-col gap-4">
      {items.map((item) => (
        <li key={item.id} className="flex gap-3">
          <span className="mt-1.5 flex flex-col items-center gap-1">
            <span
              className={`size-2 shrink-0 rounded-full ${DOT_TONE[item.tone]}`}
              aria-hidden
            />
          </span>
          <div className="flex min-w-0 flex-col leading-snug">
            <p className="text-sm text-ink-soft">
              <span className="font-semibold text-ink">{item.actor}</span> {item.action}{" "}
              <span className="font-semibold text-ink">{item.target}</span>
            </p>
            <span className="text-xs text-faint">{item.when}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}
