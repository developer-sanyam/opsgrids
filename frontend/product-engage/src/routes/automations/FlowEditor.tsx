import { useEffect, useRef } from "react";

import {
  AlertTriangleIcon,
  ArrowDown,
  ArrowUp,
  ClipboardCheckIcon,
  ClockIcon,
  CloseIcon,
  FilterIcon,
  MailIcon,
  Menu,
  SparkIcon,
} from "@opsgrids/ui";

import type { GraphNode, ValidationProblem } from "@/api/automations";
import { Button } from "@/ui";
import { NODE_LABEL, PALETTE, isWarning, summarize, tailPoint } from "./graph";
import type { AddableType, Flow, FlowSlot, FlowStep, InsertPoint, MoveDirection } from "./graph";

/**
 * The flow as a visual flowchart list of steps. A step is a real button that selects it; the
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

function StepIcon({ type, className = "size-4.5" }: { type: GraphNode["type"]; className?: string }) {
  switch (type) {
    case "trigger":
      return <SparkIcon className={className} aria-hidden />;
    case "send_message":
      return <MailIcon className={className} aria-hidden />;
    case "wait":
      return <ClockIcon className={className} aria-hidden />;
    case "condition":
      return <FilterIcon className={className} aria-hidden />;
    case "create_task":
      return <ClipboardCheckIcon className={className} aria-hidden />;
    default:
      return <SparkIcon className={className} aria-hidden />;
  }
}

function stepBadgeStyle(type: GraphNode["type"]): string {
  switch (type) {
    case "trigger":
      return "bg-blue-50 text-blue-600 border-blue-100";
    case "send_message":
      return "bg-blue-50 text-blue-600 border-blue-100";
    case "wait":
      return "bg-amber-50 text-amber-600 border-amber-200/80";
    case "condition":
      return "bg-emerald-50 text-emerald-600 border-emerald-200/80";
    case "create_task":
      return "bg-blue-50 text-blue-600 border-blue-100";
    default:
      return "bg-slate-50 text-slate-600 border-slate-200";
  }
}

function Connector() {
  return (
    <div className="flex flex-col items-center justify-center my-1" aria-hidden="true">
      <div className="h-5 w-px bg-slate-300" />
      <svg className="size-3 text-slate-400 -mt-0.5" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M2 5l4 4 4-4" />
      </svg>
    </div>
  );
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
    <div className="flex justify-center my-1">
      <Menu
        label={label}
        size="sm"
        variant="secondary"
        items={PALETTE.map((entry) => ({ label: entry.label, onSelect: () => onAdd(at, entry.type) }))}
      />
    </div>
  );
}

function ProblemList({ id, problems }: { id: string; problems: ValidationProblem[] }) {
  return (
    <ul id={id} className="mt-2.5 flex flex-col gap-1.5 rounded-xl border border-red-200/80 bg-red-50/70 p-2.5">
      {problems.map((problem) => (
        <li
          key={`${problem.code}-${problem.message}`}
          className={`flex items-start gap-2 text-xs font-medium ${isWarning(problem) ? "text-amber-800" : "text-red-700"}`}
        >
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0 text-red-600" aria-hidden />
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
  const num = numbers.get(node.id);

  return (
    <article
      className={`group relative flex min-w-0 flex-col rounded-2xl border bg-white p-3.5 transition-all shadow-2xs ${
        selected
          ? "border-blue-500 ring-2 ring-blue-500/20 shadow-xs"
          : "border-slate-200/90 hover:border-slate-300 hover:shadow-xs"
      }`}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          data-step-button={node.id}
          aria-pressed={selected}
          aria-describedby={problems.length > 0 ? problemsId : undefined}
          onClick={() => props.onSelect(node.id)}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-xl p-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <div
            className={`flex size-10 shrink-0 items-center justify-center rounded-full border ${stepBadgeStyle(
              node.type,
            )}`}
          >
            <StepIcon type={node.type} className="size-4.5" />
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <span className="sr-only">
              {num ?? ""} · {NODE_LABEL[node.type]}
            </span>
            <span aria-hidden="true" className="text-xs font-medium text-slate-500">
              {num ? `${num}. ` : ""}{NODE_LABEL[node.type]}
            </span>
            <span className="max-w-full truncate text-sm font-semibold text-slate-900">
              {summarize(node, triggerLabels)}
            </span>
          </div>
        </button>

        {removable ? (
          <div className="flex shrink-0 items-center gap-0.5">
            {node.type === "condition" ? null : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 p-0 text-slate-400 hover:text-slate-700"
                  aria-label={`Move ${name} up`}
                  disabled={!props.canMove(node.id, "up")}
                  onClick={() => props.onMove(node.id, "up")}
                >
                  <ArrowUp className="size-3.5" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 p-0 text-slate-400 hover:text-slate-700"
                  aria-label={`Move ${name} down`}
                  disabled={!props.canMove(node.id, "down")}
                  onClick={() => props.onMove(node.id, "down")}
                >
                  <ArrowDown className="size-3.5" aria-hidden />
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-slate-400 hover:text-red-600"
              aria-label={`Delete ${name}`}
              onClick={() => props.onRemove(node.id)}
            >
              <CloseIcon className="size-3.5" aria-hidden />
            </Button>
          </div>
        ) : (
          <div className="flex shrink-0 items-center text-slate-400 px-1.5 py-1">
            <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <circle cx="12" cy="12" r="1" />
              <circle cx="12" cy="5" r="1" />
              <circle cx="12" cy="19" r="1" />
            </svg>
          </div>
        )}
      </div>

      {problems.length > 0 ? <ProblemList id={problemsId} problems={problems} /> : null}
    </article>
  );
}

function BranchLabel({ label }: { label: "yes" | "no" }) {
  return (
    <div className="flex flex-col items-center">
      <span
        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${
          label === "yes"
            ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20"
            : "bg-rose-50 text-rose-700 ring-rose-600/20"
        }`}
      >
        {label === "yes" ? "Yes" : "No"}
      </span>
      <svg className="size-3 text-slate-400 my-1" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth={2}>
        <path d="M2 5l4 4 4-4" />
      </svg>
    </div>
  );
}

function Branch({ label, slot, props, parent }: { label: "yes" | "no"; slot: FlowSlot; props: FlowEditorProps; parent: string }) {
  return (
    <section
      aria-label={`${label === "yes" ? "Yes" : "No"} path of step ${props.numbers.get(parent) ?? "?"}`}
      className="flex min-w-0 flex-1 flex-col items-center gap-2"
    >
      <BranchLabel label={label} />
      <div className="w-full">
        <SlotView slot={slot} props={props} branchLabel={label} parent={parent} />
      </div>
    </section>
  );
}

function StepItem({ step, props }: { step: FlowStep; props: FlowEditorProps }) {
  const { node, branches } = step;
  const number = props.numbers.get(node.id) ?? "?";
  return (
    <li className="flex min-w-0 flex-col items-stretch w-full">
      <StepCard node={node} props={props} />
      {branches !== undefined ? (
        <div className="flex flex-col items-center w-full">
          {/* Connector branching split */}
          <div className="flex flex-col items-center w-full my-1" aria-hidden="true">
            <div className="h-4 w-px bg-slate-300" />
            <div className="w-3/4 border-t border-slate-300" />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 w-full mt-1">
            <Branch label="yes" slot={branches.yes} props={props} parent={node.id} />
            <Branch label="no" slot={branches.no} props={props} parent={node.id} />
          </div>
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
        <ol className="flex flex-col gap-0 w-full items-stretch">
          {slot.steps.map((step) => (
            <StepItem key={step.node.id} step={step} props={props} />
          ))}
        </ol>
      ) : props.readOnly ? (
        <p className="text-xs text-slate-500 text-center py-2">Nothing on this path.</p>
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
    const buttons = containerRef.current?.querySelectorAll<HTMLButtonElement>("[data-step-button]") ?? [];
    Array.from(buttons)
      .find((button) => button.dataset["stepButton"] === focusRequest.id)
      ?.focus();
  }, [focusRequest]);

  if (flow.root === null) {
    return (
      <p className="text-sm text-slate-500 text-center py-8">
        This automation has no trigger. Every automation starts from one event.
      </p>
    );
  }

  const triggerNumber = props.numbers.get(flow.root.node.id) ?? 1;
  return (
    <section ref={containerRef} aria-label="Automation flow" className="flex flex-col items-center w-full py-2">
      <h2 className="sr-only">Steps in this automation</h2>
      <div className="w-full">
        <StepCard node={flow.root.node} props={props} />
      </div>
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
          {props.readOnly ? null : <Connector />}
          <ol className="flex flex-col gap-0 w-full items-stretch">
            {flow.path.steps.map((step) => (
              <StepItem key={step.node.id} step={step} props={props} />
            ))}
          </ol>
        </>
      ) : props.readOnly ? (
        <p className="text-sm text-slate-500 py-4 text-center">No steps after the trigger.</p>
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
          className="flex flex-col gap-2 rounded-2xl border border-slate-200/80 bg-slate-50/80 p-4 mt-6 w-full"
        >
          <h3 className="text-sm font-bold text-slate-900">Not connected</h3>
          <p className="text-xs text-slate-500">The trigger can't reach these steps, so they never run.</p>
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
