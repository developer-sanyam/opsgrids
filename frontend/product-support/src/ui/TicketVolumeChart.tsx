import { useState } from "react";

import type { VolumePoint } from "@/api/types";
import { cn } from "@opsgrids/ui";

interface TicketVolumeChartProps {
  data: VolumePoint[];
}

/**
 * Tickets opened vs solved over the trailing week — grouped bars, hand-rolled with flex so it
 * stays crisp and responsive with no charting dependency. A hovered day surfaces its two numbers;
 * the series is also spelled out in an off-screen caption for screen readers.
 */
export function TicketVolumeChart({ data }: TicketVolumeChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...data.map((d) => Math.max(d.opened, d.solved)), 1);
  const totalOpened = data.reduce((s, d) => s + d.opened, 0);
  const totalSolved = data.reduce((s, d) => s + d.solved, 0);

  return (
    <figure className="m-0 flex flex-col gap-4">
      <div className="flex items-center gap-5 text-xs font-semibold text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-[3px] bg-accent" aria-hidden />
          Opened
          <span className="tnum text-ink">{totalOpened}</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-[3px] bg-present" aria-hidden />
          Solved
          <span className="tnum text-ink">{totalSolved}</span>
        </span>
      </div>

      <div
        className="flex h-48 items-end gap-2 sm:gap-3"
        role="img"
        aria-label="Tickets opened versus solved over the last seven days"
      >
        {data.map((d, i) => {
          const active = hover === i;
          return (
            <div
              key={d.date}
              className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-2"
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
            >
              <div className="relative flex h-full w-full items-end justify-center gap-1">
                {active ? (
                  <div className="absolute -top-1 left-1/2 z-10 flex -translate-x-1/2 -translate-y-full gap-2 rounded-tile bg-ink px-2.5 py-1.5 text-[0.7rem] font-semibold text-surface shadow-float">
                    <span className="text-accent-strong">
                      <span className="text-surface/60">Opened </span>
                      <span className="tnum">{d.opened}</span>
                    </span>
                    <span>
                      <span className="text-surface/60">Solved </span>
                      <span className="tnum">{d.solved}</span>
                    </span>
                  </div>
                ) : null}
                <div
                  className={cn(
                    "w-1/2 max-w-4 rounded-t-[4px] bg-accent transition-[height,opacity] duration-300",
                    hover !== null && !active && "opacity-45",
                  )}
                  style={{ height: `${(d.opened / max) * 100}%` }}
                />
                <div
                  className={cn(
                    "w-1/2 max-w-4 rounded-t-[4px] bg-present transition-[height,opacity] duration-300",
                    hover !== null && !active && "opacity-45",
                  )}
                  style={{ height: `${(d.solved / max) * 100}%` }}
                />
              </div>
              <span
                className={cn(
                  "text-[0.7rem] font-semibold tnum transition-colors",
                  active ? "text-ink" : "text-faint",
                )}
              >
                {d.label}
              </span>
            </div>
          );
        })}
      </div>

      <figcaption className="sr-only">
        {data
          .map((d) => `${d.label}: ${d.opened} opened, ${d.solved} solved`)
          .join(". ")}
        .
      </figcaption>
    </figure>
  );
}
