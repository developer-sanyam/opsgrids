import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { moveDealStage } from "@/api/client";
import type { Deal, DealStage } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyName, ownerName } from "@/data/lookups";
import { isOpen, STAGES } from "@/data/stages";
import { Avatar, CalendarIcon, GripIcon, Select, cn } from "@opsgrids/ui";
import { problemMessage, useMutation } from "@opsgrids/client";
import { formatDate, formatMoneyCompact } from "@/lib/format";

const STAGE_OPTIONS = STAGES.map((s) => ({ value: s.stage, label: s.label }));

interface DealCardProps {
  deal: Deal;
  dragging: boolean;
  canMove: boolean;
  moving: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onStageSelect: (stage: DealStage) => void;
}

/** One pipeline card: company, deal name, value, owner avatar and close date, with a drag grip
 *  and — because drag is neither keyboard- nor touch-operable — a stage `Select` every caller can
 *  use instead. */
function DealCard({
  deal,
  dragging,
  canMove,
  moving,
  onDragStart,
  onDragEnd,
  onStageSelect,
}: DealCardProps) {
  const { owners, companies } = useDirectory();
  const closed = !isOpen(deal.stage);

  return (
    <div
      draggable={canMove}
      onDragStart={canMove ? onDragStart : undefined}
      onDragEnd={canMove ? onDragEnd : undefined}
      className={cn(
        "group flex flex-col gap-3 rounded-tile border border-line bg-surface p-3.5 shadow-card",
        "transition-opacity duration-150",
        canMove && "active:cursor-grabbing cursor-grab",
        dragging && "opacity-50",
        moving && "opacity-70",
      )}
    >
      <Link to={`/deals/${deal.id}`} className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-xs font-semibold text-muted">
            {companyName(companies, deal.company_id)}
          </span>
          <span className="truncate text-sm font-bold text-ink hover:text-accent transition-colors">
            {deal.name}
          </span>
        </div>
        {canMove ? (
          <GripIcon className="size-4 shrink-0 text-faint opacity-0 transition-opacity group-hover:opacity-100" />
        ) : null}
      </Link>

      <div className="flex items-center justify-between gap-2">
        <span className="tnum text-sm font-extrabold text-ink">
          {formatMoneyCompact(deal.value_minor, deal.currency)}
        </span>
        <span className="inline-flex items-center gap-1 text-xs text-muted tnum">
          <CalendarIcon className="size-3.5 text-faint" />
          {formatDate(deal.close_date)}
        </span>
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-line pt-2.5">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <Avatar name={ownerName(owners, deal.owner_id)} size="sm" />
          <span className="truncate text-xs font-medium text-muted">{ownerName(owners, deal.owner_id)}</span>
        </span>
        <span className="tnum text-[0.7rem] font-semibold text-faint">{deal.probability}%</span>
      </div>

      {canMove ? (
        <Select
          label={`Stage — ${deal.name}`}
          value={deal.stage}
          disabled={closed || moving}
          onChange={(e) => onStageSelect(e.target.value as DealStage)}
          options={STAGE_OPTIONS}
        />
      ) : null}
    </div>
  );
}

interface KanbanBoardProps {
  groups: Record<DealStage, Deal[]>;
  /** Whether the caller may move a deal — hides the drag affordance and the stage control when
   *  false rather than rendering a control that can only ever answer 403. */
  canMove: boolean;
  onMoved: (message: string) => void;
  onMoveFailed: (message: string) => void;
  /** Refetch the underlying list once a move lands, so `groups` reflects the server's answer. */
  reload: () => void;
}

/**
 * The pipeline board: a horizontally-scrolling row of stage columns, each a stack of deal cards.
 * A drag moves a card between columns; the move is optimistic and rolls back visually on
 * failure. Every card also carries a non-drag stage `Select` — keyboard and touch have no drag
 * gesture to use here — disabled once a deal is closed, matching the server's own rule that a
 * closed deal moves nowhere.
 */
export function KanbanBoard({ groups, canMove, onMoved, onMoveFailed, reload }: KanbanBoardProps) {
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [local, setLocal] = useState(groups);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<DealStage | null>(null);

  // Resync from the server's answer whenever the parent's list reloads (after a move lands, or a
  // filter changes) — never hold a stale optimistic state longer than the request it came from.
  useEffect(() => {
    setLocal(groups);
  }, [groups]);

  const move = useMutation(
    ({ deal, stage }: { deal: Deal; stage: DealStage }) => moveDealStage(deal.id, { stage }),
    {
      onSuccess: (updated, { deal }) => {
        setMovingId(null);
        onMoved(`${deal.name} moved to ${STAGES.find((s) => s.stage === updated.stage)?.label ?? updated.stage}.`);
        reload();
      },
      onError: (error, { deal }) => {
        setMovingId(null);
        setLocal(groups); // roll back the optimistic move
        onMoveFailed(`${deal.name} couldn't be moved: ${problemMessage(error)}`);
      },
    },
  );

  function moveDeal(deal: Deal, targetStage: DealStage) {
    if (!canMove || deal.stage === targetStage || !isOpen(deal.stage) || movingId !== null) return;
    setLocal((current) => {
      const next: Record<DealStage, Deal[]> = { ...current };
      next[deal.stage] = next[deal.stage].filter((d) => d.id !== deal.id);
      next[targetStage] = [{ ...deal, stage: targetStage }, ...next[targetStage]];
      return next;
    });
    setMovingId(deal.id);
    void move.mutate({ deal, stage: targetStage });
  }

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex gap-4" style={{ minWidth: "min-content" }}>
        {STAGES.map((meta) => {
          const deals = local[meta.stage];
          const columnValue = deals.reduce((sum, d) => sum + d.value_minor, 0);
          const currency = deals[0]?.currency ?? "INR";
          return (
            <section
              key={meta.stage}
              aria-label={meta.label}
              onDragOver={
                canMove
                  ? (e) => {
                      e.preventDefault();
                      setDragOverStage(meta.stage);
                    }
                  : undefined
              }
              onDragLeave={canMove ? () => setDragOverStage(null) : undefined}
              onDrop={
                canMove
                  ? (e) => {
                      e.preventDefault();
                      setDragOverStage(null);
                      const dealId = e.dataTransfer.getData("text/plain");
                      const deal = STAGES.flatMap((s) => local[s.stage]).find((d) => d.id === dealId);
                      if (deal) moveDeal(deal, meta.stage);
                    }
                  : undefined
              }
              className={cn(
                "flex w-[264px] shrink-0 flex-col rounded-card bg-ground/60 p-2.5 transition-colors",
                dragOverStage === meta.stage && "bg-accent-soft/60 ring-2 ring-accent",
              )}
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
                    <div
                      key={deal.id}
                      onDragStart={(e) => e.dataTransfer.setData("text/plain", deal.id)}
                    >
                      <DealCard
                        deal={deal}
                        dragging={draggingId === deal.id}
                        canMove={canMove}
                        moving={movingId === deal.id}
                        onDragStart={() => setDraggingId(deal.id)}
                        onDragEnd={() => setDraggingId(null)}
                        onStageSelect={(stage) => moveDeal(deal, stage)}
                      />
                    </div>
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
