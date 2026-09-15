import { Link } from "react-router-dom";

import type { SupplierOut } from "@/api/types";
import { CATEGORY_LABEL } from "@/data/derive";
import { formatMoneyCompact } from "@/lib/format";
import { Avatar, ChevronRight } from "@opsgrids/ui";

/**
 * A compact leaderboard of suppliers by spend, each linking to its detail. On-time rate rides
 * along as a quiet secondary figure so the biggest partners' reliability is visible at a glance.
 */
export function TopSuppliers({ suppliers }: { suppliers: SupplierOut[] }) {
  return (
    <ul className="flex flex-col gap-0.5">
      {suppliers.map((s) => (
        <li key={s.id}>
          <Link
            to={`/suppliers/${s.id}`}
            className="group flex items-center gap-3 rounded-tile px-2 py-2 hover:bg-ground transition-colors"
          >
            <Avatar name={s.name} size="sm" />
            <div className="flex min-w-0 flex-1 flex-col leading-tight">
              <span className="truncate text-sm font-semibold text-ink">{s.name}</span>
              <span className="truncate text-xs text-muted">{CATEGORY_LABEL[s.category]}</span>
            </div>
            <div className="flex shrink-0 flex-col items-end leading-tight">
              <span className="tnum text-sm font-semibold text-ink">
                {formatMoneyCompact(s.spend_minor)}
              </span>
              <span className="tnum text-xs text-muted">{s.on_time_pct.toFixed(0)}% on-time</span>
            </div>
            <ChevronRight className="size-4 shrink-0 text-faint group-hover:text-ink transition-colors" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
