import { describe, expect, it } from "vitest";

import type { Graph, GraphNode, SendMessageConfig } from "@/api/automations";
import {
  addStep,
  blockingProblems,
  buildFlow,
  canMove,
  edgeFrom,
  emptyGraph,
  formatDuration,
  graphsEqual,
  groupProblems,
  isWarning,
  moveStep,
  nodeById,
  removeStep,
  replaceNode,
  splitDuration,
  stepsRemovedWith,
  summarize,
  tailPoint,
  toSeconds,
  validateGraph,
} from "./graph";
import type { TemplateCatalogue } from "./graph";

function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function ids(graph: Graph): string[] {
  return graph.nodes.map((n) => n.id).sort();
}

/** trigger -> wait_1 -> send_message_1 */
function chain(): Graph {
  let graph = emptyGraph("order.shipped");
  graph = addStep(graph, { from: "trigger" }, "wait").graph;
  graph = addStep(graph, { from: "wait_1" }, "send_message").graph;
  return graph;
}

function sendNode(config: Partial<SendMessageConfig>, id = "send_message_1"): GraphNode {
  return {
    id,
    type: "send_message",
    config: {
      channel: "whatsapp",
      template: { ref: "order_shipped" },
      recipient_path: "payload.customer.phone",
      variable_map: { "1": "payload.customer.name" },
      ...config,
    },
  };
}

const TEMPLATES: TemplateCatalogue = {
  whatsapp: [
    { ref: "order_shipped", label: "order_shipped", language: "en", preview: "", variables: ["1", "2"] },
  ],
  email: [],
};

describe("addStep", () => {
  it("appends after the trigger and after the last step", () => {
    const graph = chain();
    expect(graph.edges).toEqual([
      { from: "trigger", to: "wait_1" },
      { from: "wait_1", to: "send_message_1" },
    ]);
  });

  it("inserts in the middle and keeps the tail", () => {
    let graph = chain();
    graph = addStep(graph, { from: "trigger" }, "create_task").graph;
    expect(graph.edges).toContainEqual({ from: "trigger", to: "create_task_1" });
    expect(graph.edges).toContainEqual({ from: "create_task_1", to: "wait_1" });
    expect(graph.edges).toContainEqual({ from: "wait_1", to: "send_message_1" });
    expect(graph.edges).toHaveLength(3);
  });

  it("gives a condition a yes and a no branch, each ending in an end node", () => {
    const { graph, nodeId } = addStep(emptyGraph("x"), { from: "trigger" }, "condition");
    const yes = edgeFrom(graph, nodeId, "yes");
    const no = edgeFrom(graph, nodeId, "no");
    expect(yes).toBeDefined();
    expect(no).toBeDefined();
    expect(yes?.to).not.toBe(no?.to);
    expect(nodeById(graph, yes?.to ?? "")?.type).toBe("end");
    expect(nodeById(graph, no?.to ?? "")?.type).toBe("end");
  });

  it("puts the existing tail under yes when a condition is inserted mid-chain", () => {
    let graph = chain();
    const added = addStep(graph, { from: "wait_1" }, "condition");
    graph = added.graph;
    expect(edgeFrom(graph, added.nodeId, "yes")?.to).toBe("send_message_1");
    expect(nodeById(graph, edgeFrom(graph, added.nodeId, "no")?.to ?? "")?.type).toBe("end");
    expect(buildFlow(graph).orphans).toEqual([]);
  });

  it("adds a step to a branch, between the condition and its end", () => {
    const { graph: withCondition, nodeId } = addStep(emptyGraph("x"), { from: "trigger" }, "condition");
    const { graph } = addStep(withCondition, { from: nodeId, label: "no" }, "create_task");
    const flow = buildFlow(graph);
    const condition = flow.path?.steps[0];
    expect(condition?.branches?.no.steps.map((s) => s.node.id)).toEqual(["create_task_1"]);
    expect(condition?.branches?.yes.steps).toEqual([]);
    // The branch still ends in an end node, so the condition keeps both edges.
    expect(edgeFrom(graph, "create_task_1")?.to).toMatch(/^end_/);
  });

  it("refuses a condition insert without a branch", () => {
    const { graph, nodeId } = addStep(emptyGraph("x"), { from: "trigger" }, "condition");
    expect(() => addStep(graph, { from: nodeId }, "wait")).toThrow();
  });

  it("does not mutate the graph it was given", () => {
    const graph = deepFreeze(chain());
    expect(() => addStep(graph, { from: "trigger" }, "condition")).not.toThrow();
  });
});

describe("removeStep", () => {
  it("splices a plain step out and reconnects its neighbours", () => {
    const graph = removeStep(chain(), "wait_1");
    expect(ids(graph)).toEqual(["send_message_1", "trigger"]);
    expect(graph.edges).toEqual([{ from: "trigger", to: "send_message_1" }]);
  });

  it("never removes the trigger", () => {
    const graph = chain();
    expect(removeStep(graph, "trigger")).toBe(graph);
  });

  it("gives a branch an end node back when its only step is removed", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "wait").graph;
    graph = removeStep(graph, "wait_1");
    const yes = edgeFrom(graph, "condition_1", "yes");
    expect(nodeById(graph, yes?.to ?? "")?.type).toBe("end");
    expect(edgeFrom(graph, "condition_1", "no")).toBeDefined();
  });

  it("removes a condition with everything on both branches, and says how many steps that is", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "wait").graph;
    graph = addStep(graph, { from: "condition_1", label: "no" }, "create_task").graph;
    expect(stepsRemovedWith(graph, "condition_1")).toBe(2);
    expect(stepsRemovedWith(graph, "wait_1")).toBe(0);
    const after = removeStep(graph, "condition_1");
    expect(ids(after)).toEqual(["trigger"]);
    expect(after.edges).toEqual([]);
  });

  it("does not mutate the graph it was given", () => {
    const graph = deepFreeze(chain());
    expect(removeStep(graph, "wait_1").nodes).toHaveLength(2);
    expect(graph.nodes).toHaveLength(3);
  });
});

describe("moveStep", () => {
  it("swaps a step with the one after it", () => {
    const graph = moveStep(chain(), "wait_1", "down");
    expect(graph.edges).toContainEqual({ from: "trigger", to: "send_message_1" });
    expect(graph.edges).toContainEqual({ from: "send_message_1", to: "wait_1" });
    expect(graph.edges).toHaveLength(2);
  });

  it("moves up by swapping with the one before", () => {
    const graph = moveStep(chain(), "send_message_1", "up");
    expect(graph.edges).toContainEqual({ from: "trigger", to: "send_message_1" });
    expect(graph.edges).toContainEqual({ from: "send_message_1", to: "wait_1" });
  });

  it("keeps a three-step chain connected after a middle swap", () => {
    let graph = chain();
    graph = addStep(graph, { from: "send_message_1" }, "create_task").graph;
    graph = moveStep(graph, "wait_1", "down");
    const order = buildFlow(graph).path?.steps.map((s) => s.node.id);
    expect(order).toEqual(["send_message_1", "wait_1", "create_task_1"]);
  });

  it("does not move the first or last step outward, the trigger, or a condition", () => {
    let graph = chain();
    expect(canMove(graph, "wait_1", "up")).toBe(false);
    expect(canMove(graph, "send_message_1", "down")).toBe(false);
    expect(canMove(graph, "trigger", "down")).toBe(false);
    graph = addStep(graph, { from: "send_message_1" }, "condition").graph;
    expect(canMove(graph, "condition_1", "up")).toBe(false);
    expect(canMove(graph, "send_message_1", "down")).toBe(false);
    expect(moveStep(graph, "wait_1", "up")).toBe(graph);
  });

  it("does not move a branch's first step up into the condition", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "wait").graph;
    expect(canMove(graph, "wait_1", "up")).toBe(false);
  });
});

describe("buildFlow", () => {
  it("lists steps in order, with branches on the condition", () => {
    let graph = chain();
    graph = addStep(graph, { from: "send_message_1" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "create_task").graph;
    const flow = buildFlow(graph);
    expect(flow.root?.node.type).toBe("trigger");
    expect(flow.path?.steps.map((s) => s.node.id)).toEqual(["wait_1", "send_message_1", "condition_1"]);
    expect(tailPoint(flow.path ?? { at: { from: "trigger" }, steps: [] })).toEqual({ from: "condition_1" });
    expect(flow.orphans).toEqual([]);
  });

  it("reports nodes the trigger cannot reach", () => {
    const graph = chain();
    const stray: GraphNode = { id: "wait_9", type: "wait", config: { seconds: 60 } };
    const flow = buildFlow({ nodes: [...graph.nodes, stray], edges: graph.edges });
    expect(flow.orphans.map((n) => n.id)).toEqual(["wait_9"]);
  });

  it("does not loop forever on a cyclic graph from the server", () => {
    const graph: Graph = {
      nodes: [
        { id: "trigger", type: "trigger", config: { event_type: "x" } },
        { id: "a", type: "wait", config: { seconds: 60 } },
      ],
      edges: [
        { from: "trigger", to: "a" },
        { from: "a", to: "trigger" },
      ],
    };
    expect(buildFlow(graph).path?.steps.map((s) => s.node.id)).toEqual(["a"]);
  });
});

describe("replaceNode and equality", () => {
  it("replaces one node and leaves the rest", () => {
    const graph = chain();
    const next = replaceNode(graph, { id: "wait_1", type: "wait", config: { seconds: 7200 } });
    expect(nodeById(next, "wait_1")).toEqual({ id: "wait_1", type: "wait", config: { seconds: 7200 } });
    expect(next.edges).toBe(graph.edges);
    expect(graphsEqual(graph, next)).toBe(false);
    expect(graphsEqual(graph, chain())).toBe(true);
  });
});

describe("durations", () => {
  it("chooses the largest unit that divides evenly", () => {
    expect(splitDuration(600)).toEqual({ value: 10, unit: "minutes" });
    expect(splitDuration(7200)).toEqual({ value: 2, unit: "hours" });
    expect(splitDuration(259_200)).toEqual({ value: 3, unit: "days" });
    expect(splitDuration(5400)).toEqual({ value: 90, unit: "minutes" });
  });

  it("round-trips and formats", () => {
    expect(toSeconds(3, "days")).toBe(259_200);
    expect(formatDuration(60)).toBe("1 minute");
    expect(formatDuration(600)).toBe("10 minutes");
    expect(formatDuration(86_400)).toBe("1 day");
  });
});

describe("summarize", () => {
  it("names each step in words", () => {
    expect(summarize({ id: "t", type: "trigger", config: { event_type: "order.shipped" } }, { "order.shipped": "Order shipped" })).toBe("Order shipped");
    expect(summarize({ id: "t", type: "trigger", config: { event_type: "" } })).toBe("Choose the event that starts this automation");
    expect(summarize({ id: "w", type: "wait", config: { seconds: 600 } })).toBe("10 minutes");
    expect(summarize(sendNode({ channel: "email", template: { ref: "" } }))).toBe("Email: choose a template");
    expect(summarize(sendNode({}))).toBe("WhatsApp: order_shipped");
  });
});

describe("validateGraph", () => {
  it("accepts a complete flow", () => {
    let graph = emptyGraph("order.shipped");
    graph = addStep(graph, { from: "trigger" }, "send_message").graph;
    graph = replaceNode(graph, sendNode({ variable_map: { "1": "payload.a", "2": "payload.b" } }));
    expect(validateGraph(graph, TEMPLATES)).toEqual([]);
  });

  it("asks for a trigger event", () => {
    const problems = validateGraph(emptyGraph(""));
    expect(problems).toEqual([expect.objectContaining({ node_id: "trigger", code: "trigger_event_missing" })]);
  });

  it("reports a missing trigger against the flow, not a step", () => {
    const problems = validateGraph({ nodes: [], edges: [] });
    expect(problems).toEqual([expect.objectContaining({ node_id: null, code: "trigger_count" })]);
  });

  it("reports every unmapped template variable on the send step", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "send_message").graph;
    graph = replaceNode(graph, sendNode({ variable_map: { "1": "payload.a" } }));
    const problems = validateGraph(graph, TEMPLATES);
    expect(problems.map((p) => p.message)).toEqual(["Map a value for {{2}}."]);
    expect(problems[0]?.node_id).toBe("send_message_1");
  });

  it("flags a template that is not in the catalogue", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "send_message").graph;
    graph = replaceNode(graph, sendNode({ template: { ref: "gone" } }));
    expect(validateGraph(graph, TEMPLATES).map((p) => p.code)).toContain("template_unknown");
  });

  it("checks condition rules and wait range", () => {
    let graph = addStep(emptyGraph("x"), { from: "trigger" }, "condition").graph;
    graph = addStep(graph, { from: "condition_1", label: "yes" }, "wait").graph;
    graph = replaceNode(graph, { id: "wait_1", type: "wait", config: { seconds: 30 } });
    const codes = validateGraph(graph).map((p) => p.code);
    expect(codes).toContain("condition_rules_missing");
    expect(codes).toContain("wait_out_of_range");
    graph = replaceNode(graph, {
      id: "condition_1",
      type: "condition",
      config: { match: "all", rules: [{ path: "payload.total", op: "gt" }] },
    });
    expect(validateGraph(graph).map((p) => p.code)).toContain("condition_rule_value");
  });

  it("detects a cycle and an unreachable step", () => {
    const graph: Graph = {
      nodes: [
        { id: "trigger", type: "trigger", config: { event_type: "x" } },
        { id: "a", type: "wait", config: { seconds: 60 } },
        { id: "b", type: "wait", config: { seconds: 60 } },
      ],
      edges: [
        { from: "trigger", to: "a" },
        { from: "a", to: "trigger" },
      ],
    };
    const problems = validateGraph(graph);
    expect(problems.map((p) => p.code)).toContain("cycle");
    expect(problems).toContainEqual(expect.objectContaining({ node_id: "b", code: "unreachable" }));
  });
});

describe("problem display mapping", () => {
  const problems = [
    { node_id: null, code: "trigger_count", message: "This automation needs a trigger." },
    { node_id: "wait_1", code: "wait_out_of_range", message: "Wait between 1 minute and 30 days." },
    { node_id: "wait_1", code: "server_wait", message: "Wait between 1 minute and 30 days." },
    { node_id: "send_message_1", code: "channel_not_connected", message: "WhatsApp isn't connected." },
    { node_id: "send_message_1", code: "template_missing", message: "Choose a template to send." },
  ];

  it("puts flow-wide problems on top, the rest on their step, without repeats", () => {
    const grouped = groupProblems(problems);
    expect(grouped.general).toHaveLength(1);
    expect(grouped.byNode["wait_1"]).toHaveLength(1);
    expect(grouped.byNode["send_message_1"]).toHaveLength(2);
  });

  it("treats a channel that is not connected as a warning that does not block", () => {
    expect(isWarning(problems[3] as (typeof problems)[number])).toBe(true);
    expect(blockingProblems(problems).map((p) => p.code)).not.toContain("channel_not_connected");
    expect(blockingProblems(problems)).toHaveLength(4);
  });
});
