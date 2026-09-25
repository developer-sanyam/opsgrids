import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { Card } from "./Card";
import { SampleChip } from "./SampleChip";
import { ChartSkeleton } from "./Skeletons";

interface ChartCardProps {
  /** "Account growth", "Revenue by plan". */
  title: string;
  /** One line under the title: the period, the unit, what is counted. */
  description?: string | undefined;
  /** The control at the top right: a period `Select`, a segmented `Tabs`, a "View all" link. */
  action?: ReactNode | undefined;
  /**
   * Marks the series as representative rather than live — the amber chip. Every chart backed by a
   * service that is not wired yet carries it; a sample chart that does not is a lie on a
   * dashboard.
   */
  sample?: boolean | { label?: string | undefined; title?: string | undefined } | undefined;
  /** The series is loading: a placeholder in the chart's shape, sized like it. */
  loading?: boolean | undefined;
  /** The shape of the placeholder. Default `area`. */
  skeleton?: "area" | "bar" | "donut" | undefined;
  /** The fetch failed: an `ErrorState` in the chart's place. */
  error?: ReactNode | undefined;
  /** There is nothing to plot yet: an `EmptyState` in the chart's place. */
  empty?: ReactNode | undefined;
  /** `2` on a dashboard (the default); `3` inside a section that already has an `h2`. */
  headingLevel?: 2 | 3 | undefined;
  /** The chart. */
  children: ReactNode;
  className?: string | undefined;
}

/**
 * The card every dashboard chart sits in (brief §5, §18): title, an optional control, and the
 * chart — or its loading, empty or error state in the same place, so the grid never jumps.
 *
 * It is a `<section>` named by its title, so a dashboard's charts are in the landmark list. State
 * wins in the order a screen shows it: loading, then error, then empty, then the chart.
 */
export function ChartCard({
  title,
  description,
  action,
  sample = false,
  loading = false,
  skeleton = "area",
  error,
  empty,
  headingLevel = 2,
  children,
  className,
}: ChartCardProps) {
  const headingId = `${useId()}-title`;
  const Heading = headingLevel === 2 ? "h2" : "h3";

  return (
    <Card className={cn("flex min-w-0 flex-col gap-4 p-4 sm:p-6", className)}>
      <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex flex-wrap items-center gap-2">
              <Heading id={headingId} className="text-base font-bold text-ink">
                {title}
              </Heading>
              {sample ? <SampleChip {...(sample === true ? {} : sample)} /> : null}
            </div>
            {description ? <p className="text-sm text-muted">{description}</p> : null}
          </div>
          {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
        {loading ? <ChartSkeleton variant={skeleton} label={`Loading ${title}`} /> : (error ?? empty ?? children)}
      </section>
    </Card>
  );
}
