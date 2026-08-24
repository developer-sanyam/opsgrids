import type { DeptHeadcount } from "@/api/types";

/** A stable dot/bar colour per rank, drawn from the token palette. */
const PALETTE = [
  "var(--color-accent)",
  "var(--color-info)",
  "var(--color-cyan)",
  "var(--color-present)",
  "var(--color-late)",
];

interface HeadcountByDepartmentProps {
  data: DeptHeadcount[];
  total: number;
}

/**
 * Headcount grouped by department as horizontal bars: a coloured dot, the name, the count and its
 * share, with a track showing the proportion. Derived live from the employee list.
 */
export function HeadcountByDepartment({ data, total }: HeadcountByDepartmentProps) {
  const largest = data[0];
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3.5">
        {data.map((dept, i) => {
          const color = PALETTE[i % PALETTE.length];
          return (
            <li key={dept.key} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                  aria-hidden
                />
                <span className="font-semibold text-ink">{dept.name}</span>
                <span className="ml-auto tnum font-semibold text-ink">{dept.count}</span>
                <span className="w-10 text-right tnum text-muted">{dept.pct}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.max(dept.pct, 3)}%`, backgroundColor: color }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      {largest ? (
        <p className="border-t border-line pt-3 text-sm text-muted">
          Largest team{" "}
          <span className="font-semibold text-ink">{largest.name}</span>
          {" · "}
          <span className="tnum font-semibold text-ink">{largest.pct}%</span> of{" "}
          <span className="tnum">{total}</span> people
        </p>
      ) : null}
    </div>
  );
}
