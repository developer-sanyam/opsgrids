/**
 * The automation graph as the builder edits it: pure functions over the contract's
 * `{nodes, edges}` JSON, and nothing else.
 *
 * Every function takes a graph and returns a new one. Nothing here mutates its argument, so React
 * state can hold a graph and compare it by reference, and a failed save can put the previous graph
 * back untouched.
 *
 * **The editor's model is a tree, and the wire format is the tree flattened.** A step has one
 * successor, except a `condition`, which has exactly one `yes` and one `no` edge and takes the rest
 * of its path with it. A branch that has no steps ends in an `end` node, because the contract wants
 * both edges present. Inserting a condition in the middle of a chain keeps the old tail — under
 * `yes` — so nothing is orphaned by adding a decision.
 *
 * Reordering swaps two neighbouring plain steps. A condition is not reorderable: it owns the path
 * after it, so moving it would silently change what the branches mean. Delete and re-add it.
 *
 * Validation here mirrors the service's publish checks so a person is told before they press the
 * button; it never replaces them. The service's own problems are merged in by the caller.
 */

import type {
  ConditionConfig,
  ConditionOp,
  EdgeLabel,
  Graph,
  GraphEdge,
  GraphNode,
  MessageChannel,
  NodeType,
  TemplateOption,
  ValidationProblem,
} from "@/api/automations";

/* --------------------------------- vocabulary --------------------------------- */

export type AddableType = "condition" | "wait" | "send_message" | "create_task";

export const NODE_LABEL: Record<NodeType, string> = {
  trigger: "Trigger",
  condition: "Condition",
  wait: "Wait",
  send_message: "Send message",
  create_task: "Create task",
  end: "End",
};

/** The step palette, in the order it is offered. */
export const PALETTE: { type: AddableType; label: string; hint: string }[] = [
  { type: "condition", label: "Condition", hint: "Split the flow into a yes path and a no path." },
  { type: "wait", label: "Wait", hint: "Pause before the next step, optionally until an allowed time." },
  { type: "send_message", label: "Send message", hint: "Send a WhatsApp template or an email." },
  { type: "create_task", label: "Create task", hint: "Ask a team to follow up by hand." },
];

export const OP_LABEL: Record<ConditionOp, string> = {
  eq: "is equal to",
  neq: "is not equal to",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  contains: "contains",
  in: "is one of",
  exists: "exists",
};

export const MIN_WAIT_SECONDS = 60;
export const MAX_WAIT_SECONDS = 2_592_000;

/* ----------------------------------- lookups ----------------------------------- */

export function nodeById(graph: Graph, id: string): GraphNode | undefined {
  return graph.nodes.find((n) => n.id === id);
}

export function outgoing(graph: Graph, id: string): GraphEdge[] {
  return graph.edges.filter((e) => e.from === id);
}

/** The edge leaving `id` with this label (or no label). */
export function edgeFrom(graph: Graph, id: string, label?: EdgeLabel): GraphEdge | undefined {
  return graph.edges.find((e) => e.from === id && e.label === label);
}

export function incoming(graph: Graph, id: string): GraphEdge | undefined {
  return graph.edges.find((e) => e.to === id);
}

export function triggerNodes(graph: Graph): GraphNode[] {
  return graph.nodes.filter((n) => n.type === "trigger");
}

/* ----------------------------------- creation ----------------------------------- */

function newNodeId(graph: Graph, type: NodeType): string {
  const taken = new Set(graph.nodes.map((n) => n.id));
  if (type === "trigger" && !taken.has("trigger")) return "trigger";
  let n = 1;
  while (taken.has(`${type}_${n}`)) n += 1;
  return `${type}_${n}`;
}

export function defaultNode(id: string, type: NodeType): GraphNode {
  switch (type) {
    case "trigger":
      return { id, type, config: { event_type: "" } };
    case "condition":
      return { id, type, config: { match: "all", rules: [] } };
    case "wait":
      return { id, type, config: { seconds: 3600 } };
    case "send_message":
      return {
        id,
        type,
        config: { channel: "whatsapp", template: { ref: "" }, recipient_path: "", variable_map: {} },
      };
    case "create_task":
      return { id, type, config: { title: "" } };
    case "end":
      return { id, type, config: {} };
  }
}

/** A new automation: a trigger and nothing after it. */
export function emptyGraph(eventType = ""): Graph {
  return { nodes: [{ id: "trigger", type: "trigger", config: { event_type: eventType } }], edges: [] };
}

function edge(from: string, to: string, label?: EdgeLabel): GraphEdge {
  return label === undefined ? { from, to } : { from, to, label };
}

/* ----------------------------------- editing ----------------------------------- */

/** Where a step goes: after `from`, or on one branch of the condition `from`. */
export interface InsertPoint {
  from: string;
  label?: EdgeLabel;
}

/** Insert a step at `at`. The step that used to follow keeps following — under `yes`, for a
 *  condition. Throws for an insert point that cannot exist, which is a bug in the caller. */
export function addStep(
  graph: Graph,
  at: InsertPoint,
  type: AddableType,
): { graph: Graph; nodeId: string } {
  const source = nodeById(graph, at.from);
  if (source === undefined || source.type === "end") {
    throw new Error(`Cannot add a step after ${at.from}`);
  }
  const label = source.type === "condition" ? at.label : undefined;
  if (source.type === "condition" && label === undefined) {
    throw new Error("A condition's steps go on its yes or no branch");
  }

  const existing = edgeFrom(graph, at.from, label);
  const id = newNodeId(graph, type);
  const created = defaultNode(id, type);
  const nodes = [...graph.nodes, created];
  const edges = graph.edges.filter((e) => e !== existing);
  edges.push(edge(at.from, id, label));

  if (type === "condition") {
    if (existing !== undefined) {
      edges.push(edge(id, existing.to, "yes"));
    } else {
      const yesEnd = defaultNode(newNodeId({ nodes, edges }, "end"), "end");
      nodes.push(yesEnd);
      edges.push(edge(id, yesEnd.id, "yes"));
    }
    const noEnd = defaultNode(newNodeId({ nodes, edges }, "end"), "end");
    nodes.push(noEnd);
    edges.push(edge(id, noEnd.id, "no"));
  } else if (existing !== undefined) {
    edges.push(edge(id, existing.to));
  }
  return { graph: { nodes, edges }, nodeId: id };
}

/** Every node id at or below `id`. */
export function subtreeIds(graph: Graph, id: string): string[] {
  const seen = new Set<string>();
  const queue = [id];
  while (queue.length > 0) {
    const next = queue.shift();
    if (next === undefined || seen.has(next)) continue;
    seen.add(next);
    for (const e of outgoing(graph, next)) queue.push(e.to);
  }
  return [...seen];
}

/** How many real steps (not `end` markers) removing `id` takes with it besides `id` itself.
 *  Zero for a plain step, which is spliced out rather than cut off. */
export function stepsRemovedWith(graph: Graph, id: string): number {
  const node = nodeById(graph, id);
  if (node?.type !== "condition") return 0;
  return subtreeIds(graph, id).filter((other) => {
    const n = nodeById(graph, other);
    return other !== id && n !== undefined && n.type !== "end";
  }).length;
}

/** Remove a step. A plain step is spliced out and its successor takes its place; a condition goes
 *  with everything on both branches. The trigger cannot be removed. */
export function removeStep(graph: Graph, id: string): Graph {
  const node = nodeById(graph, id);
  if (node === undefined || node.type === "trigger" || node.type === "end") return graph;

  const drop = new Set(node.type === "condition" ? subtreeIds(graph, id) : [id]);
  const inEdge = incoming(graph, id);
  const outEdge = node.type === "condition" ? undefined : edgeFrom(graph, id);

  const nodes = graph.nodes.filter((n) => !drop.has(n.id));
  const edges = graph.edges.filter((e) => !drop.has(e.from) && !drop.has(e.to));

  if (inEdge !== undefined && !drop.has(inEdge.from)) {
    if (outEdge !== undefined) {
      edges.push(edge(inEdge.from, outEdge.to, inEdge.label));
    } else if (inEdge.label !== undefined) {
      const end = defaultNode(newNodeId({ nodes, edges }, "end"), "end");
      nodes.push(end);
      edges.push(edge(inEdge.from, end.id, inEdge.label));
    }
  }
  return { nodes, edges };
}

function movable(node: GraphNode | undefined): boolean {
  return node?.type === "wait" || node?.type === "send_message" || node?.type === "create_task";
}

export type MoveDirection = "up" | "down";

/** The neighbour a step would swap with, if it may move that way at all. */
function swapPartner(graph: Graph, id: string, direction: MoveDirection): GraphNode | undefined {
  const node = nodeById(graph, id);
  if (!movable(node)) return undefined;
  if (direction === "down") {
    const next = edgeFrom(graph, id);
    const other = next === undefined ? undefined : nodeById(graph, next.to);
    return movable(other) ? other : undefined;
  }
  const previous = incoming(graph, id);
  if (previous === undefined || previous.label !== undefined) return undefined;
  const other = nodeById(graph, previous.from);
  return movable(other) ? other : undefined;
}

export function canMove(graph: Graph, id: string, direction: MoveDirection): boolean {
  return swapPartner(graph, id, direction) !== undefined;
}

/** Swap a plain step with its neighbour. A step that cannot move that way is left where it is. */
export function moveStep(graph: Graph, id: string, direction: MoveDirection): Graph {
  const partner = swapPartner(graph, id, direction);
  if (partner === undefined) return graph;
  const first = direction === "down" ? id : partner.id;
  const second = direction === "down" ? partner.id : id;

  const before = incoming(graph, first);
  const middle = edgeFrom(graph, first);
  const after = edgeFrom(graph, second);
  if (before === undefined || middle === undefined) return graph;

  const edges = graph.edges.filter((e) => e !== before && e !== middle && e !== after);
  edges.push(edge(before.from, second, before.label));
  edges.push(edge(second, first));
  if (after !== undefined) edges.push(edge(first, after.to));
  return { nodes: graph.nodes, edges };
}

/** Replace a node by id with a whole new node. */
export function replaceNode(graph: Graph, node: GraphNode): Graph {
  return { nodes: graph.nodes.map((n) => (n.id === node.id ? node : n)), edges: graph.edges };
}

export function graphsEqual(a: Graph, b: Graph): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/* ----------------------------------- flow tree ----------------------------------- */

export interface FlowStep {
  node: GraphNode;
  /** A condition's two paths; a plain step has none. */
  branches?: { yes: FlowSlot; no: FlowSlot };
}

/** A run of steps, and where the next one would be inserted if it is empty. */
export interface FlowSlot {
  at: InsertPoint;
  steps: FlowStep[];
}

export interface Flow {
  /** The trigger and everything after it; null when the graph has no trigger. */
  root: FlowStep | null;
  /** The plain steps after the trigger, as a slot. */
  path: FlowSlot | null;
  /** Nodes the trigger cannot reach. The editor lists them so they can be deleted. */
  orphans: GraphNode[];
}

function walk(graph: Graph, at: InsertPoint, seen: Set<string>): FlowSlot {
  const steps: FlowStep[] = [];
  let edgeOut = edgeFrom(graph, at.from, at.label);
  while (edgeOut !== undefined) {
    const node = nodeById(graph, edgeOut.to);
    if (node === undefined || seen.has(node.id)) break;
    seen.add(node.id);
    if (node.type === "end") break;
    if (node.type === "condition") {
      steps.push({
        node,
        branches: {
          yes: walk(graph, { from: node.id, label: "yes" }, seen),
          no: walk(graph, { from: node.id, label: "no" }, seen),
        },
      });
      break;
    }
    steps.push({ node });
    edgeOut = edgeFrom(graph, node.id);
  }
  return { at, steps };
}

export function buildFlow(graph: Graph): Flow {
  const trigger = triggerNodes(graph)[0];
  if (trigger === undefined) return { root: null, path: null, orphans: [...graph.nodes] };
  const seen = new Set<string>([trigger.id]);
  const path = walk(graph, { from: trigger.id }, seen);
  const orphans = graph.nodes.filter((n) => !seen.has(n.id) && n.type !== "end");
  return { root: { node: trigger }, path, orphans };
}

/** The insert point for adding a step after the last one in a slot. */
export function tailPoint(slot: FlowSlot): InsertPoint {
  const last = slot.steps[slot.steps.length - 1];
  if (last === undefined) return slot.at;
  return { from: last.node.id };
}

/* ------------------------------------- units ------------------------------------- */

export type DurationUnit = "minutes" | "hours" | "days";
const UNIT_SECONDS: Record<DurationUnit, number> = { minutes: 60, hours: 3600, days: 86_400 };

export function toSeconds(value: number, unit: DurationUnit): number {
  return Math.round(value * UNIT_SECONDS[unit]);
}

/** The largest unit that divides `seconds` evenly, so 7200 reads as "2 hours" and 90 minutes as
 *  "90 minutes" rather than "1.5 hours". */
export function splitDuration(seconds: number): { value: number; unit: DurationUnit } {
  if (seconds >= UNIT_SECONDS.days && seconds % UNIT_SECONDS.days === 0) {
    return { value: seconds / UNIT_SECONDS.days, unit: "days" };
  }
  if (seconds >= UNIT_SECONDS.hours && seconds % UNIT_SECONDS.hours === 0) {
    return { value: seconds / UNIT_SECONDS.hours, unit: "hours" };
  }
  return { value: Math.round(seconds / UNIT_SECONDS.minutes), unit: "minutes" };
}

function plural(n: number, one: string): string {
  return `${n} ${n === 1 ? one : `${one}s`}`;
}

export function formatDuration(seconds: number): string {
  const { value, unit } = splitDuration(seconds);
  return plural(value, unit.slice(0, -1));
}

/* -------------------------------------- summaries -------------------------------------- */

const CHANNEL_LABEL: Record<MessageChannel, string> = { whatsapp: "WhatsApp", email: "Email" };

export function channelLabel(channel: MessageChannel): string {
  return CHANNEL_LABEL[channel];
}

/** One line for a step's row in the flow. */
export function summarize(node: GraphNode, triggerLabels: Record<string, string> = {}): string {
  switch (node.type) {
    case "trigger":
      return node.config.event_type === ""
        ? "Choose the event that starts this automation"
        : (triggerLabels[node.config.event_type] ?? node.config.event_type);
    case "condition": {
      const n = node.config.rules.length;
      if (n === 0) return "No rules yet";
      const joiner = node.config.match === "all" ? "all" : "any";
      return `${plural(n, "rule")}, ${joiner} must match`;
    }
    case "wait":
      return formatDuration(node.config.seconds);
    case "send_message": {
      const ref = node.config.template.ref;
      return ref === ""
        ? `${CHANNEL_LABEL[node.config.channel]}: choose a template`
        : `${CHANNEL_LABEL[node.config.channel]}: ${ref}`;
    }
    case "create_task":
      return node.config.title === "" ? "Give the task a title" : node.config.title;
    case "end":
      return "End of this path";
  }
}

/* ------------------------------------- validation ------------------------------------- */

/** A problem the service reports as advice rather than a blocker: the channel is not connected
 *  yet, which does not stop a publish. */
export function isWarning(problem: ValidationProblem): boolean {
  return problem.code.endsWith("not_connected");
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

export interface TemplateCatalogue {
  whatsapp: TemplateOption[];
  email: TemplateOption[];
}

function reachable(graph: Graph, from: string): Set<string> {
  return new Set(subtreeIds(graph, from));
}

function hasCycle(graph: Graph): boolean {
  const state = new Map<string, "open" | "done">();
  function visit(id: string): boolean {
    const known = state.get(id);
    if (known === "open") return true;
    if (known === "done") return false;
    state.set(id, "open");
    for (const e of outgoing(graph, id)) if (visit(e.to)) return true;
    state.set(id, "done");
    return false;
  }
  return graph.nodes.some((n) => visit(n.id));
}

function conditionProblems(node: GraphNode & { type: "condition" }, graph: Graph): ValidationProblem[] {
  const problems: ValidationProblem[] = [];
  const config: ConditionConfig = node.config;
  if (edgeFrom(graph, node.id, "yes") === undefined || edgeFrom(graph, node.id, "no") === undefined) {
    problems.push({ node_id: node.id, code: "condition_branches", message: "A condition needs both a yes path and a no path." });
  }
  if (config.rules.length === 0) {
    problems.push({ node_id: node.id, code: "condition_rules_missing", message: "Add at least one rule." });
  }
  config.rules.forEach((rule, i) => {
    if (rule.path.trim() === "") {
      problems.push({ node_id: node.id, code: "condition_rule_path", message: `Rule ${i + 1}: choose the field to check.` });
    }
    if (rule.op !== "exists" && (rule.value === undefined || rule.value === "")) {
      problems.push({ node_id: node.id, code: "condition_rule_value", message: `Rule ${i + 1}: enter a value to compare with.` });
    }
  });
  return problems;
}

function sendProblems(
  node: GraphNode & { type: "send_message" },
  templates: TemplateCatalogue | undefined,
): ValidationProblem[] {
  const problems: ValidationProblem[] = [];
  const { channel, template, recipient_path, variable_map } = node.config;
  if (template.ref === "") {
    problems.push({ node_id: node.id, code: "template_missing", message: "Choose a template to send." });
  }
  if (recipient_path.trim() === "") {
    problems.push({ node_id: node.id, code: "recipient_missing", message: "Choose where the recipient's address comes from." });
  }
  const found = templates?.[channel].find((t) => t.ref === template.ref);
  if (template.ref !== "" && templates !== undefined && found === undefined) {
    problems.push({
      node_id: node.id,
      code: "template_unknown",
      message: `${CHANNEL_LABEL[channel]} template “${template.ref}” isn't available. Choose another.`,
    });
  }
  for (const key of found?.variables ?? []) {
    const mapped = variable_map[key];
    if (mapped === undefined || mapped.trim() === "") {
      problems.push({ node_id: node.id, code: "variable_unmapped", message: `Map a value for {{${key}}}.` });
    }
  }
  return problems;
}

/**
 * The publish checks the client can run on its own. The service runs them again and has the last
 * word; this is so a person sees what is wrong before pressing the button, not after.
 */
export function validateGraph(graph: Graph, templates?: TemplateCatalogue): ValidationProblem[] {
  const problems: ValidationProblem[] = [];
  const triggers = triggerNodes(graph);
  if (triggers.length !== 1) {
    problems.push({
      node_id: null,
      code: "trigger_count",
      message: triggers.length === 0 ? "This automation needs a trigger." : "An automation has exactly one trigger.",
    });
  }
  const first = triggers[0];
  const live = first === undefined ? new Set<string>() : reachable(graph, first.id);

  if (hasCycle(graph)) {
    problems.push({ node_id: null, code: "cycle", message: "The flow loops back on itself. Remove the step that points backwards." });
  }

  for (const node of graph.nodes) {
    if (first !== undefined && !live.has(node.id) && node.type !== "end") {
      problems.push({ node_id: node.id, code: "unreachable", message: "This step isn't connected to the trigger." });
    }
    switch (node.type) {
      case "trigger":
        if (node.config.event_type === "") {
          problems.push({ node_id: node.id, code: "trigger_event_missing", message: "Choose the event that starts this automation." });
        }
        break;
      case "condition":
        problems.push(...conditionProblems(node, graph));
        break;
      case "wait": {
        const { seconds, window } = node.config;
        if (!Number.isInteger(seconds) || seconds < MIN_WAIT_SECONDS || seconds > MAX_WAIT_SECONDS) {
          problems.push({ node_id: node.id, code: "wait_out_of_range", message: "Wait between 1 minute and 30 days." });
        }
        if (window !== undefined && (!TIME.test(window.start) || !TIME.test(window.end))) {
          problems.push({ node_id: node.id, code: "wait_window", message: "Enter the allowed window as start and end times, like 09:00." });
        }
        break;
      }
      case "send_message":
        problems.push(...sendProblems(node, templates));
        break;
      case "create_task":
        if (node.config.title.trim() === "") {
          problems.push({ node_id: node.id, code: "task_title_missing", message: "Give the task a title." });
        }
        break;
      case "end":
        break;
    }
  }
  return problems;
}

export interface GroupedProblems {
  /** Problems that belong to the flow as a whole. */
  general: ValidationProblem[];
  byNode: Record<string, ValidationProblem[]>;
}

/** Fold a problem list into what the screen shows: flow-wide ones at the top, the rest on the
 *  step they name. The same problem reported by the client and by the service appears once. */
export function groupProblems(problems: ValidationProblem[]): GroupedProblems {
  const grouped: GroupedProblems = { general: [], byNode: {} };
  const seen = new Set<string>();
  for (const problem of problems) {
    const key = `${problem.node_id ?? ""}|${problem.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (problem.node_id === null) {
      grouped.general.push(problem);
    } else {
      (grouped.byNode[problem.node_id] ??= []).push(problem);
    }
  }
  return grouped;
}

/** Problems that stop a publish. Warnings do not. */
export function blockingProblems(problems: ValidationProblem[]): ValidationProblem[] {
  return problems.filter((p) => !isWarning(p));
}
