/**
 * Automations (contract slice A1) — the workflow engine's typed client.
 *
 * Every path is under `/api/v1/engage/automations`, gated on the `engage` entitlement. Nothing
 * here is enforcement: the service refuses an edit to a non-draft, a publish that fails
 * validation and a retry of a run that did not fail, whatever this client offers.
 *
 * The graph types are the contract's own. `position` is accepted from the wire and never
 * produced: this editor is a structured vertical flow, not a canvas, so it has no coordinates to
 * write and preserves the ones it is given.
 */

import { ApiError, apiRequest } from "@opsgrids/client";

import { FULL_PAGE } from "./client";
import type { Page } from "./types";

/* ---------------------------------- graph ---------------------------------- */

export type AutomationStatus = "draft" | "active" | "paused";
export type NodeType = "trigger" | "condition" | "wait" | "send_message" | "create_task" | "end";
export type EdgeLabel = "yes" | "no";
export type MessageChannel = "whatsapp" | "email";
export type MessagePurpose = "transactional" | "marketing";
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

/* --------------------------------- requests --------------------------------- */

const BASE = "/engage/automations";

function idPath(id: string): string {
  return `${BASE}/${encodeURIComponent(id)}`;
}

/** What a caller of the list may narrow by; each is the service's own filter. */
export interface ListAutomationsOptions {
  status?: AutomationStatus;
  module?: string;
  q?: string;
  page?: number;
  size?: number;
}

/** `GET /engage/automations?status&module&q&page`. */
export async function listAutomations(
  options: ListAutomationsOptions = {},
): Promise<Page<AutomationOut>> {
  const { status, module, q, page = 1, size = 25 } = options;
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (status !== undefined) query.set("status", status);
  if (module !== undefined && module !== "") query.set("module", module);
  if (q !== undefined && q.trim() !== "") query.set("q", q.trim());
  return apiRequest<Page<AutomationOut>>(`${BASE}?${query.toString()}`);
}

/** `GET /engage/automations/triggers`: the events an automation may start from. */
export async function listTriggers(): Promise<TriggerOut[]> {
  const result = await apiRequest<{ items: TriggerOut[] }>(`${BASE}/triggers`);
  return result.items;
}

/** `POST /engage/automations`: 201, always a draft. */
export async function createAutomation(input: AutomationIn): Promise<AutomationDetailOut> {
  return apiRequest<AutomationDetailOut>(BASE, { method: "POST", body: input });
}

/** `GET /engage/automations/{id}`. */
export async function getAutomation(id: string): Promise<AutomationDetailOut> {
  return apiRequest<AutomationDetailOut>(idPath(id));
}

/** `PATCH /engage/automations/{id}`: drafts only; the service refuses anything else. */
export async function updateAutomation(
  id: string,
  input: AutomationPatch,
): Promise<AutomationDetailOut> {
  return apiRequest<AutomationDetailOut>(idPath(id), { method: "PATCH", body: input });
}

/** `DELETE /engage/automations/{id}`: draft or paused only. */
export async function deleteAutomation(id: string): Promise<void> {
  await apiRequest<void>(idPath(id), { method: "DELETE" });
}

/** `POST /engage/automations/{id}/publish`: validates, freezes a version, goes `active`. A 422
 *  carries the problems; `problemsFrom` reads them off the error. */
export async function publishAutomation(id: string): Promise<AutomationDetailOut> {
  return apiRequest<AutomationDetailOut>(`${idPath(id)}/publish`, { method: "POST" });
}

/** `POST /engage/automations/{id}/pause`. */
export async function pauseAutomation(id: string): Promise<AutomationOut> {
  return apiRequest<AutomationOut>(`${idPath(id)}/pause`, { method: "POST" });
}

/** `POST /engage/automations/{id}/resume`. */
export async function resumeAutomation(id: string): Promise<AutomationOut> {
  return apiRequest<AutomationOut>(`${idPath(id)}/resume`, { method: "POST" });
}

/** `GET /engage/automations/{id}/runs?state&page`. */
export async function listRuns(
  automationId: string,
  options: { state?: RunState; page?: number; size?: number } = {},
): Promise<Page<RunOut>> {
  const { state, page = 1, size = 25 } = options;
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (state !== undefined) query.set("state", state);
  return apiRequest<Page<RunOut>>(`${idPath(automationId)}/runs?${query.toString()}`);
}

/** `GET /engage/automations/runs/{run_id}`. */
export async function getRun(runId: string): Promise<RunDetailOut> {
  return apiRequest<RunDetailOut>(`${BASE}/runs/${encodeURIComponent(runId)}`);
}

/** `POST /engage/automations/runs/{run_id}/retry`: failed runs only. */
export async function retryRun(runId: string): Promise<{ ok: true }> {
  return apiRequest<{ ok: true }>(`${BASE}/runs/${encodeURIComponent(runId)}/retry`, {
    method: "POST",
  });
}

/** `GET /engage/automations/stats?from&to`, dates as `YYYY-MM-DD`. */
export async function getStats(range: { from?: string; to?: string } = {}): Promise<StatsOut> {
  const query = new URLSearchParams();
  if (range.from !== undefined) query.set("from", range.from);
  if (range.to !== undefined) query.set("to", range.to);
  const suffix = query.toString();
  return apiRequest<StatsOut>(`${BASE}/stats${suffix === "" ? "" : `?${suffix}`}`);
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

interface WhatsAppTemplateRow {
  /** Contract W2 name; the earlier shape called it `provider_name`. */
  name?: string;
  provider_name?: string;
  language?: string;
  status?: string;
  body_preview?: string;
  preview_text?: string;
  variables?: { index?: number; name?: string; key?: string; position?: number }[];
}

interface EmailTemplateRow {
  id: string;
  subject: string | null;
  description: string;
  placeholders: string[];
}

function whatsAppOption(row: WhatsAppTemplateRow): TemplateOption | null {
  const ref = row.name ?? row.provider_name;
  if (ref === undefined || ref === "") return null;
  const variables = (row.variables ?? []).map((v, i) =>
    String(v.index ?? v.position ?? (v.key !== undefined && v.key !== "" ? v.key : i + 1)),
  );
  return {
    ref,
    label: ref,
    language: row.language ?? null,
    preview: row.body_preview ?? row.preview_text ?? "",
    variables,
  };
}

/** Approved WhatsApp templates: `GET /engage/whatsapp/templates`. A template that is not
 *  approved cannot send, so it is not offered. */
export async function listWhatsAppTemplateOptions(): Promise<TemplateOption[]> {
  const query = new URLSearchParams({ status: "approved", size: String(FULL_PAGE) });
  const page = await apiRequest<Page<WhatsAppTemplateRow>>(
    `/engage/whatsapp/templates?${query.toString()}`,
  );
  return page.items
    .filter((row) => row.status === undefined || row.status === "approved")
    .flatMap((row) => {
      const option = whatsAppOption(row);
      return option === null ? [] : [option];
    });
}

/** Email templates: `GET /notifications/templates?channel=email`. */
export async function listEmailTemplateOptions(): Promise<TemplateOption[]> {
  const query = new URLSearchParams({ channel: "email", size: String(FULL_PAGE) });
  const page = await apiRequest<Page<EmailTemplateRow>>(
    `/notifications/templates?${query.toString()}`,
  );
  return page.items.map((row) => ({
    ref: row.id,
    label: row.id,
    language: null,
    preview: row.subject ?? row.description,
    variables: row.placeholders,
  }));
}

/* --------------------------------- problems --------------------------------- */

function isProblem(value: unknown): value is ValidationProblem {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry["code"] === "string" &&
    typeof entry["message"] === "string" &&
    (entry["node_id"] === null || entry["node_id"] === undefined || typeof entry["node_id"] === "string")
  );
}

/**
 * The validation problems a failed publish or save carries. The contract's 422 puts them in a
 * `problems` member beside the standard problem+json fields; a plain field-error list is read as
 * problems that belong to no step. An error that carries neither yields an empty list, so the
 * caller falls back to the error's own sentence.
 */
export function problemsFrom(error: unknown): ValidationProblem[] {
  if (!(error instanceof ApiError) || error.problem === null) return [];
  const body = error.problem as Record<string, unknown>;
  const raw = body["problems"];
  if (Array.isArray(raw)) {
    return raw.filter(isProblem).map((p) => ({
      node_id: p.node_id ?? null,
      code: p.code,
      message: p.message,
    }));
  }
  return (error.problem.errors ?? []).map((e) => ({
    node_id: null,
    code: e.code,
    message: e.message,
  }));
}
