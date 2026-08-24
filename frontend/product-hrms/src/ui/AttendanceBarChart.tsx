import type { WeekdayAttendance } from "@/api/types";

interface AttendanceBarChartProps {
  data: WeekdayAttendance[];
  /** For the y-axis / bar scale; usually total headcount. */
  max: number;
}

/**
 * The wide weekly attendance chart. Rounded mint bars (one highlighted), a faint track behind
 * each, weekday labels below. Built as a real, labelled figure — the values are read out for
 * assistive tech rather than left to the visual alone.
 */
export function AttendanceBarChart({ data, max }: AttendanceBarChartProps) {
  const scaleMax = Math.max(max, ...data.map((d) => d.present));

  return (
    <figure className="mt-2">
      <div className="flex items-end justify-between gap-3 sm:gap-5 h-44">
        {data.map((d) => {
          const pct = (d.present / scaleMax) * 100;
          return (
            <div
              key={d.label}
              className="flex flex-1 flex-col items-center gap-3 h-full justify-end"
            >
              <div className="relative flex w-full max-w-12 flex-1 items-end justify-center">
                {/* faint full-height track */}
                <div className="absolute inset-x-0 bottom-0 top-0 rounded-full bg-ground" />
                <div
                  className={`relative w-full rounded-full ${
                    d.highlight ? "bg-mint" : "bg-[#cbd3ca]"
                  }`}
                  style={{ height: `${Math.max(pct, 6)}%` }}
                  title={`${d.label}: ${d.present} present`}
                >
                  {d.highlight ? (
                    <span className="absolute -top-7 left-1/2 -translate-x-1/2 rounded-full bg-ink px-2 py-0.5 text-[0.7rem] font-bold text-white tnum">
                      {d.present}
                    </span>
                  ) : null}
                </div>
              </div>
              <span className="text-xs font-medium text-muted">{d.label}</span>
            </div>
          );
        })}
      </div>
      <figcaption className="sr-only">
        Present headcount this week:{" "}
        {data.map((d) => `${d.label} ${d.present}`).join(", ")}.
      </figcaption>
    </figure>
  );
}
