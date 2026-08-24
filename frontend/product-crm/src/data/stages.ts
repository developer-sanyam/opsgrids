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

/** The four open (in-flight) stages, in order — the working columns of the pipeline board. */
export const OPEN_STAGES: DealStage[] = ["lead", "qualified", "proposal", "negotiation"];

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

export function isWon(stage: DealStage): boolean {
  return stage === "closed_won";
}
