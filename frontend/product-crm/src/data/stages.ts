/**
 * Pipeline stage reference: order, labels, and which stages are open / won / lost. The board and
 * every rollup read this so the stage vocabulary lives in exactly one place.
 */
import type { DealStage } from "@/api/types";

export interface StageMeta {
  stage: DealStage;
  label: string;
  short: string;
  /** A token colour used for the stage's accent dot / bar. */
  color: string;
}

/** Every stage, in pipeline order. The two terminal stages come last. */
export const STAGES: StageMeta[] = [
  { stage: "lead", label: "Lead", short: "Lead", color: "var(--color-faint)" },
  { stage: "qualified", label: "Qualified", short: "Qual.", color: "var(--color-info)" },
  { stage: "proposal", label: "Proposal", short: "Prop.", color: "var(--color-accent)" },
  { stage: "negotiation", label: "Negotiation", short: "Nego.", color: "var(--color-late)" },
  { stage: "closed_won", label: "Closed Won", short: "Won", color: "var(--color-present)" },
  { stage: "closed_lost", label: "Closed Lost", short: "Lost", color: "var(--color-danger)" },
];

/**
 * Where a deal's probability lands when a stage move does not name one, in percent. The deal
 * service applies these itself (`STAGE_DEFAULT_PROBABILITY` in its `services.py`), on the board and
 * from a sheet alike; they are written here only so the words shown to a person can say what a move
 * will set. A move to a closed stage that names none ends at 100 (won) or 0 (lost).
 */
export const STAGE_DEFAULT_PROBABILITY: Readonly<Record<DealStage, number>> = {
  lead: 20,
  qualified: 40,
  proposal: 55,
  negotiation: 70,
  closed_won: 100,
  closed_lost: 0,
};

const META = new Map(STAGES.map((s) => [s.stage, s]));

export function stageMeta(stage: DealStage): StageMeta {
  return META.get(stage) ?? STAGES[0]!;
}

export function stageLabel(stage: DealStage): string {
  return stageMeta(stage).label;
}

export function isOpen(stage: DealStage): boolean {
  return stage !== "closed_won" && stage !== "closed_lost";
}
