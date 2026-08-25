import { Link } from "react-router-dom";

import type { AttentionItem } from "@/api/types";
import { AlertTriangleIcon, ChevronRight, PackageIcon, TruckIcon, cn } from "@atmora/ui";

const KIND_ICON: Record<AttentionItem["kind"], typeof PackageIcon> = {
  out_of_stock: AlertTriangleIcon,
  low_stock: PackageIcon,
  late_delivery: TruckIcon,
};

const TONE_CHIP: Record<AttentionItem["tone"], string> = {
  danger: "bg-danger-soft text-danger",
  late: "bg-late-soft text-late",
};

const TONE_LABEL: Record<AttentionItem["kind"], string> = {
  out_of_stock: "Out of stock",
  low_stock: "Low stock",
  late_delivery: "Late delivery",
};

/**
 * The "needs attention" queue: low / out-of-stock items and late deliveries. Each row is a link
 * to where the problem is fixed, with an icon, a labelled severity chip (never colour alone) and
 * a short explanation.
 */
export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((item) => {
        const Icon = KIND_ICON[item.kind];
        return (
          <li key={item.id}>
            <Link
              to={item.href}
              className="group flex items-center gap-3 rounded-tile px-2.5 py-2.5 hover:bg-ground transition-colors"
            >
              <span
                className={cn(
                  "grid size-9 shrink-0 place-items-center rounded-tile",
                  TONE_CHIP[item.tone],
                )}
              >
                <Icon className="size-[18px]" strokeWidth={1.9} />
              </span>
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="truncate text-sm font-semibold text-ink">{item.title}</span>
                <span className="truncate text-xs text-muted">{item.detail}</span>
              </div>
              <span
                className={cn(
                  "hidden shrink-0 rounded-full px-2 py-0.5 text-[0.68rem] font-bold sm:inline-flex",
                  TONE_CHIP[item.tone],
                )}
              >
                {TONE_LABEL[item.kind]}
              </span>
              <ChevronRight className="size-4 shrink-0 text-faint group-hover:text-ink transition-colors" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
