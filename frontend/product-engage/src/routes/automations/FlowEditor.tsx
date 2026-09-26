import { useEffect, useRef } from "react";

import { AlertTriangleIcon, ArrowDown, ArrowUp, CloseIcon, Menu } from "@opsgrids/ui";

import type { GraphNode, ValidationProblem } from "@/api/automations";
import { Button } from "@/ui";
import { NODE_LABEL, PALETTE, isWarning, summarize, tailPoint } from "./graph";
import type { AddableType, Flow, FlowSlot, FlowStep, InsertPoint, MoveDirection } from "./graph";

/**
 * The flow as a vertical list of steps, not a canvas. A step is a real button that selects it; the
 * controls to move or delete it are real buttons beside it; "Add step" is a real menu after each
 * step and at the start of each empty branch. Everything is reachable by Tab and operable with the
 * keyboard, and every control names the step it acts on ("Delete step 3, Wait") because a flow can
 * hold several steps of one kind.
 *
 * A condition's two paths sit side by side under it, each its own labelled region. Problems for a
 * step are written on the step itself, with a word as well as an icon, so no state is colour alone.
 */

export interface FlowEditorProps {
  flow: Flow;
  selectedId: string | null;
  numbers: Map<string, number>;
  triggerLabels: Record<string, string>;
  problemsByNode: Record<string, ValidationProblem[]>;
  readOnly: boolean;
  /** Ask for focus on a step's button; the nonce makes the same step focusable twice. */
  focusRequest: { id: string; nonce: number } | null;
  onSelect: (id: string) => void;
  onAdd: (at: InsertPoint, type: AddableType) => void;
  onRemove: (id: string) => void;
  onMove: (id: string, direction: MoveDirection) => void;
  canMove: (id: string, direction: MoveDirection) => boolean;
}

function stepName(node: GraphNode, numbers: Map<string, number>): string {
  return `step ${numbers.get(node.id) ?? "?"}, ${NODE_LABEL[node.type]}`;
}

function Connector() {
  return <span aria-hidden className="ml-6 block h-4 w-px bg-line" />;
}

function AddStep({
  at,
  label,
  onAdd,
}: {
  at: InsertPoint;
  label: string;
  onAdd: (at: InsertPoint, type: AddableType) => void;
}) {
  return (
    <Menu
      label={label}
      size="sm"
      variant="secondary"
      items={PALETTE.map((entry) => ({ label: entry.label, onSelect: () => onAdd(at, entry.type) }))}
    />
  );
}

function ProblemList({ id, problems }: { id: string; problems: ValidationProblem[] }) {
  return (
    <ul id={id} className="flex flex-col gap-1 border-t border-line pt-2">
      {problems.map((problem) => (
        <li
          key={`${problem.code}-${problem.message}`}
          className={`flex items-start gap-2 text-sm ${isWarning(problem) ? "text-late" : "text-negative"}`}
        >
          <AlertTriangleIcon className="mt-1 size-4 shrink-0" aria-hidden />
          <span>
            <span className="sr-only">{isWarning(problem) ? "Warning: " : "Problem: "}</span>
            {problem.message}
          </span>
        </li>
      ))}
    </ul>
  );
}

function StepCard({
  node,
  props,
}: {
  node: GraphNode;
  props: FlowEditorProps;
}) {
  const { selectedId, numbers, triggerLabels, problemsByNode, readOnly } = props;
  const selected = selectedId === node.id;
  const problems = problemsByNode[node.id] ?? [];
  const name = stepName(node, numbers);
  const problemsId = `step-problems-${node.id}`;
  const removable = node.type !== "trigger" && !readOnly;

  return (
    <article
      className={`flex flex-col gap-2 rounded-card bg-surface p-3 shadow-card ${
        selected ? "outline outline-2 outline-accent" : ""
      }`}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-step-button={node.id}
          aria-pressed={selected}
          aria-describedby={problems.length > 0 ? problemsId : undefined}
          onClick={() => props.onSelect(node.id)}
          className="flex min-w-0 flex-1 flex-col items-start gap-1 rounded-tile px-2 py-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span className="text-xs font-bold uppercase tracking-wider text-faint">
            {numbers.get(node.id) ?? ""} · {NODE_LABEL[node.type]}
          </span>
          <span className="max-w-full truncate text-sm font-semibold text-ink">
            {summarize(node, triggerLabels)}
          </span>
        </button>
        {removable ? (
          <div className="flex shrink-0 items-center">
            {node.type === "condition" ? null : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${name} up`}
                  disabled={!props.canMove(node.id, "up")}
                  onClick={() => props.onMove(node.id, "up")}
                >
                  <ArrowUp className="size-4" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Move ${name} down`}
                  disabled={!props.canMove(node.id, "down")}
                  onClick={() => props.onMove(node.id, "down")}
                >
                  <ArrowDown className="size-4" aria-hidden />
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="text-negative"
              aria-label={`Delete ${name}`}
              onClick={() => props.onRemove(node.id)}
            >
              <CloseIcon className="size-4" aria-hidden />
            </Button>
          </div>
        ) : null}
      </div>
      {problems.length > 0 ? <ProblemList id={problemsId} problems={problems} /> : null}
    </article>
  );
}

function BranchLabel({ label }: { label: "yes" | "no" }) {
  return (
    <h3 className="text-xs font-bold uppercase tracking-wider text-muted">
      {label === "yes" ? "Yes" : "No"}
    </h3>
  );
}

function Branch({ label, slot, props, parent }: { label: "yes" | "no"; slot: FlowSlot; props: FlowEditorProps; parent: string }) {
  return (
    <section
      aria-label={`${label === "yes" ? "Yes" : "No"} path of step ${props.numbers.get(parent) ?? "?"}`}
      className="flex min-w-0 flex-col gap-3 rounded-tile bg-sunken p-3"
    >
      <BranchLabel label={label} />
      <SlotView slot={slot} props={props} branchLabel={label} parent={parent} />
    </section>
  );
}

function StepItem({ step, props }: { step: FlowStep; props: FlowEditorProps }) {
  const { node, branches } = step;
  const number = props.numbers.get(node.id) ?? "?";
  return (
    <li className="flex flex-col gap-3">
      <StepCard node={node} props={props} />
      {branches !== undefined ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Branch label="yes" slot={branches.yes} props={props} parent={node.id} />
          <Branch label="no" slot={branches.no} props={props} parent={node.id} />
        </div>
      ) : (
        <>
          <Connector />
          {props.readOnly ? null : (
            <AddStep at={{ from: node.id }} label={`Add step after step ${number}`} onAdd={props.onAdd} />
          )}
        </>
      )}
    </li>
  );
}

function SlotView({
  slot,
  props,
  branchLabel,
  parent,
}: {
  slot: FlowSlot;
  props: FlowEditorProps;
  branchLabel?: "yes" | "no";
  parent?: string;
}) {
  const emptyLabel =
    branchLabel !== undefined && parent !== undefined
      ? `Add step to ${branchLabel} path of step ${props.numbers.get(parent) ?? "?"}`
      : "Add step";
  return (
    <>
      {slot.steps.length > 0 ? (
        <ol className="flex flex-col gap-3">
          {slot.steps.map((step) => (
            <StepItem key={step.node.id} step={step} props={props} />
          ))}
        </ol>
      ) : props.readOnly ? (
        <p className="text-sm text-muted">Nothing on this path.</p>
      ) : (
        <AddStep at={tailPoint(slot)} label={emptyLabel} onAdd={props.onAdd} />
      )}
    </>
  );
}

export function FlowEditor(props: FlowEditorProps) {
  const { flow, focusRequest } = props;
  const containerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (focusRequest === null) return;
    containerRef.current
      ?.querySelector<HTMLButtonElement>(`[data-step-button="${CSS.escape(focusRequest.id)}"]`)
      ?.focus();
  }, [focusRequest]);

  if (flow.root === null) {
    return (
      <p className="text-sm text-muted">
        This automation has no trigger. Every automation starts from one event.
      </p>
    );
  }

  const triggerNumber = props.numbers.get(flow.root.node.id) ?? 1;
  return (
    <section ref={containerRef} aria-label="Automation flow" className="flex flex-col gap-3">
      <StepCard node={flow.root.node} props={props} />
      <Connector />
      {flow.path !== null && flow.path.steps.length > 0 ? (
        <>
          {props.readOnly ? null : (
            <AddStep
              at={{ from: flow.root.node.id }}
              label={`Add step after step ${triggerNumber}`}
              onAdd={props.onAdd}
            />
          )}
          <Connector />
          <ol className="flex flex-col gap-3">
            {flow.path.steps.map((step) => (
              <StepItem key={step.node.id} step={step} props={props} />
            ))}
          </ol>
        </>
      ) : props.readOnly ? (
        <p className="text-sm text-muted">No steps after the trigger.</p>
      ) : (
        <AddStep
          at={{ from: flow.root.node.id }}
          label={`Add step after step ${triggerNumber}`}
          onAdd={props.onAdd}
        />
      )}

      {flow.orphans.length > 0 ? (
        <section
          aria-label="Steps not connected to the trigger"
          className="flex flex-col gap-2 rounded-tile bg-sunken p-3"
        >
          <h3 className="text-sm font-bold text-ink">Not connected</h3>
          <p className="text-sm text-muted">The trigger can't reach these steps, so they never run.</p>
          <ul className="flex flex-col gap-2">
            {flow.orphans.map((node) => (
              <li key={node.id}>
                <StepCard node={node} props={props} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
