/** The request and response shapes of `./automations`, kept apart so fixtures can import them without importing the requests. */

/* ---------------------------------- graph ---------------------------------- */

export type AutomationStatus = "draft" | "active" | "paused";

export type NodeType = "trigger" | "condition" | "wait" | "send_message" | "create_task" | "end";

export type EdgeLabel = "yes" | "no";

export type MessageChannel = "whatsapp" | "email";

export type MessagePurpose = "transactional" | "marketing";

/** What `recipient_path` points at: an address (the default) or a CRM contact id, which the
    service turns into an address when the message is sent. */
export type RecipientKind = "address" | "contact_id";

export type ConditionOp = "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "contains" | "in" | "exists";

export interface NodePosition {
  x: number;
  y: number;
}

export interface TriggerConfig {
  event_type: string;
}

export interface ConditionRule {
  path: string;
  op: ConditionOp;
  /** Absent for `exists`. */
  value?: string | number | boolean | (string | number)[];
}

export interface ConditionConfig {
  match: "all" | "any";
  rules: ConditionRule[];
}

export interface WaitWindow {
  start: string;
  end: string;
  timezone: string;
}

export interface WaitConfig {
  /** 60 to 2 592 000 (thirty days). */
  seconds: number;
  window?: WaitWindow;
}

export interface SendMessageConfig {
  channel: MessageChannel;
  template: { ref: string; language?: string };
  recipient_path: string;
  /** Absent means `address`. `contact_id` makes the service look the contact up when the message
   *  is sent; it is valid on WhatsApp only, until customer can answer an email address. */
  recipient_kind?: RecipientKind;
  /** Template variable key ("1", "order_number") to a `payload.*` or `run.*` path. */
  variable_map: Record<string, string>;
  purpose?: MessagePurpose;
}

export interface CreateTaskConfig {
  title: string;
  description?: string;
  assignee_team?: string;
}

export type EndConfig = Record<string, never>;

interface NodeBase {
  id: string;
  position?: NodePosition;
}

export type GraphNode =
  | (NodeBase & { type: "trigger"; config: TriggerConfig })
  | (NodeBase & { type: "condition"; config: ConditionConfig })
  | (NodeBase & { type: "wait"; config: WaitConfig })
  | (NodeBase & { type: "send_message"; config: SendMessageConfig })
  | (NodeBase & { type: "create_task"; config: CreateTaskConfig })
  | (NodeBase & { type: "end"; config: EndConfig });

export interface GraphEdge {
  from: string;
  to: string;
  label?: EdgeLabel;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/* -------------------------------- automations -------------------------------- */

export interface ValidationProblem {
  node_id: string | null;
  code: string;
  message: string;
}

export interface Validation {
  ok: boolean;
  problems: ValidationProblem[];
}

/** `Automation` in the contract: the list row. */
export interface AutomationOut {
  id: string;
  name: string;
  description: string;
  status: AutomationStatus;
  trigger_event: string;
  module: string;
  version: number | null;
  executions_30d: number;
  success_rate_30d: number | null;
  last_run_at: string | null;
  updated_at: string;
}

/** `GET /engage/automations/{id}`: the row plus its graph and the server's own validation. */
export interface AutomationDetailOut extends AutomationOut {
  graph: Graph;
  validation: Validation;
}

export interface AutomationIn {
  name: string;
  description?: string;
  graph: Graph;
}

export interface AutomationPatch {
  name?: string;
  description?: string;
  graph?: Graph;
}

export interface TriggerVariable {
  path: string;
  label: string;
  kind: string;
}

export interface TriggerOut {
  event_type: string;
  label: string;
  module: string;
  variables: TriggerVariable[];
}

/* ----------------------------------- runs ----------------------------------- */

export type RunState = "running" | "waiting" | "completed" | "failed" | "skipped";

export type StepState = "waiting" | "running" | "done" | "failed" | "skipped";

export interface RunOut {
  id: string;
  automation_id: string;
  version: number;
  state: RunState;
  started_at: string;
  finished_at: string | null;
  source_event_id: string;
  /** Masked by the service. */
  subject: string;
  failure_code: string | null;
  next_wake_at: string | null;
}

export interface RunStepOut {
  node_id: string;
  type: NodeType;
  state: StepState;
  started_at: string | null;
  finished_at: string | null;
  attempt: number;
  detail: string | null;
  message_id: string | null;
}

export interface RunDetailOut extends RunOut {
  steps: RunStepOut[];
  idempotency_key: string;
}

/* ---------------------------------- stats ---------------------------------- */

export interface StatsSeriesPoint {
  date: string;
  successful: number;
  failed: number;
}

export interface FailureReason {
  code: string;
  label: string;
  count: number;
}

export interface AutomationPerformance {
  automation_id: string;
  name: string;
  executions: number;
  success_rate: number;
  failed: number;
  avg_duration_ms: number | null;
  last_run_at: string | null;
}

export interface StatsOut {
  executions: number;
  successful: number;
  failed: number;
  avg_duration_ms: number | null;
  series: StatsSeriesPoint[];
  failures_by_reason: FailureReason[];
  per_automation: AutomationPerformance[];
}

/** What a caller of the list may narrow by; each is the service's own filter. */
export interface ListAutomationsOptions {
  status?: AutomationStatus;
  module?: string;
  q?: string;
  page?: number;
  size?: number;
}

/* ------------------------------ template options ------------------------------ */

/**
 * One template a send step may point at, whichever channel it is on. `ref` is what the graph
 * stores; `variables` are the keys the step's `variable_map` must cover.
 */
export interface TemplateOption {
  ref: string;
  label: string;
  language: string | null;
  preview: string;
  variables: string[];
}
