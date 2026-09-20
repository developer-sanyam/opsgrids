import { useState } from "react";
import { Link } from "react-router-dom";

import type { Deal, DealStage } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyName, ownerName } from "@/data/lookups";
import { STAGES } from "@/data/stages";
import { Avatar, CalendarIcon, GripIcon, cn } from "@opsgrids/ui";
import { formatDate, formatMoneyCompact } from "@/lib/format";

interface DealCardProps {
  deal: Deal;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
}

/** One pipeline card: company, deal name, value, owner avatar and close date, with a drag grip. */
function DealCard({ deal, dragging, onDragStart, onDragEnd }: DealCardProps) {
  const { owners, companies } = useDirectory();
  return (
    <Link
      to={`/deals/${deal.id}`}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        "group block rounded-tile border border-line bg-surface p-3.5 shadow-card",
        "transition-transform duration-150 hover:-translate-y-0.5 active:cursor-grabbing cursor-grab",
        dragging && "opacity-50",
      )}
    >
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-xs font-semibold text-muted">
            {companyName(companies, deal.company_id)}
          </span>
          <span className="truncate text-sm font-bold text-ink">{deal.name}</span>
        </div>
        <GripIcon className="size-4 shrink-0 text-faint opacity-0 transition-opacity group-hover:opacity-100" />
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="tnum text-sm font-extrabold text-ink">
          {formatMoneyCompact(deal.value_minor, deal.currency)}
        </span>
        <span className="inline-flex items-center gap-1 text-xs text-muted tnum">
          <CalendarIcon className="size-3.5 text-faint" />
          {formatDate(deal.close_date)}
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2.5">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <Avatar name={ownerName(owners, deal.owner_id)} size="sm" />
          <span className="truncate text-xs font-medium text-muted">{ownerName(owners, deal.owner_id)}</span>
        </span>
        <span className="tnum text-[0.7rem] font-semibold text-faint">{deal.probability}%</span>
      </div>
    </Link>
  );
}

interface KanbanBoardProps {
  groups: Record<DealStage, Deal[]>;
}

/**
 * The pipeline board: a horizontally-scrolling row of stage columns, each a stack of deal cards.
 * Cards are grab-styled and draggable for affordance; reordering is a later slice, so a drag just
 * lifts the card visually. Every card links to its deal.
 */
export function KanbanBoard({ groups }: KanbanBoardProps) {
  const [draggingId, setDraggingId] = useState<string | null>(null);

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex gap-4" style={{ minWidth: "min-content" }}>
        {STAGES.map((meta) => {
          const deals = groups[meta.stage];
          const columnValue = deals.reduce((sum, d) => sum + d.value_minor, 0);
          const currency = deals[0]?.currency ?? "INR";
          return (
            <section
              key={meta.stage}
              aria-label={meta.label}
              className="flex w-[264px] shrink-0 flex-col rounded-card bg-ground/60 p-2.5"
            >
              <header className="flex items-center gap-2 px-1.5 pb-2.5">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: meta.color }}
                  aria-hidden
                />
                <span className="text-sm font-bold text-ink">{meta.label}</span>
                <span className="grid h-5 min-w-5 place-items-center rounded-full bg-surface px-1.5 text-[0.7rem] font-bold text-muted tnum">
                  {deals.length}
                </span>
                <span className="ml-auto tnum text-xs font-semibold text-muted">
                  {formatMoneyCompact(columnValue, currency)}
                </span>
              </header>

              <div className="flex min-h-16 flex-col gap-2.5">
                {deals.length === 0 ? (
                  <p className="rounded-tile border border-dashed border-line py-6 text-center text-xs text-faint">
                    No deals
                  </p>
                ) : (
                  deals.map((deal) => (
                    <DealCard
                      key={deal.id}
                      deal={deal}
                      dragging={draggingId === deal.id}
                      onDragStart={() => setDraggingId(deal.id)}
                      onDragEnd={() => setDraggingId(null)}
                    />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
