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

import { FULL_PAGE, USE_FIXTURES, fixturePage, refuseWithoutService, settle } from "./client";
import { AUTOMATION_RUNS, AUTOMATIONS, EMAIL_TEMPLATE_OPTIONS, WHATSAPP_TEMPLATE_OPTIONS, automationDetail } from "./sample.fixtures";
import { RUN_DETAIL_FAILED, STATS, TRIGGERS } from "./automations.fixtures";
import type { Page } from "./types";
export type {
  AutomationStatus,
  NodeType,
  EdgeLabel,
  MessageChannel,
  MessagePurpose,
  RecipientKind,
  ConditionOp,
  NodePosition,
  TriggerConfig,
  ConditionRule,
  ConditionConfig,
  WaitWindow,
  WaitConfig,
  SendMessageConfig,
  CreateTaskConfig,
  EndConfig,
  GraphNode,
  GraphEdge,
  Graph,
  ValidationProblem,
  Validation,
  AutomationOut,
  AutomationDetailOut,
  AutomationIn,
  AutomationPatch,
  TriggerVariable,
  TriggerOut,
  RunState,
  StepState,
  RunOut,
  RunStepOut,
  RunDetailOut,
  StatsSeriesPoint,
  FailureReason,
  AutomationPerformance,
  StatsOut,
  ListAutomationsOptions,
  TemplateOption,
} from "./automations.types";
import type {
  ValidationProblem,
  AutomationOut,
  AutomationDetailOut,
  AutomationIn,
  AutomationPatch,
  TriggerOut,
  RunState,
  RunOut,
  RunStepOut,
  RunDetailOut,
  StatsOut,
  ListAutomationsOptions,
  TemplateOption,
} from "./automations.types";

/* --------------------------------- requests --------------------------------- */

const BASE = "/engage/automations";

function idPath(id: string): string {
  return `${BASE}/${encodeURIComponent(id)}`;
}

/** `GET /engage/automations?status&module&q&page`. */
export async function listAutomations(
  options: ListAutomationsOptions = {},
): Promise<Page<AutomationOut>> {
  const { status, module, q, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const needle = (q ?? "").trim().toLowerCase();
    const rows = AUTOMATIONS.filter(
      (a) =>
        (status === undefined || a.status === status) &&
        (module === undefined || module === "" || a.module === module) &&
        (needle === "" || a.name.toLowerCase().includes(needle)),
    );
    return settle(fixturePage(rows));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (status !== undefined) query.set("status", status);
  if (module !== undefined && module !== "") query.set("module", module);
  if (q !== undefined && q.trim() !== "") query.set("q", q.trim());
  return apiRequest<Page<AutomationOut>>(`${BASE}?${query.toString()}`);
}

/** `GET /engage/automations/triggers`: the events an automation may start from. */
export async function listTriggers(): Promise<TriggerOut[]> {
  if (USE_FIXTURES) return settle(TRIGGERS);
  const result = await apiRequest<{ items: TriggerOut[] }>(`${BASE}/triggers`);
  return result.items;
}

/** `POST /engage/automations`: 201, always a draft. Only the row is relied on: the graph and the
 *  service's validation are read back with `getAutomation`. */
export async function createAutomation(input: AutomationIn): Promise<AutomationOut> {
  if (USE_FIXTURES) refuseWithoutService("Creating an automation");
  return apiRequest<AutomationOut>(BASE, { method: "POST", body: input });
}

/** `GET /engage/automations/{id}`. */
export async function getAutomation(id: string): Promise<AutomationDetailOut> {
  if (USE_FIXTURES) {
    const found = automationDetail(id);
    if (found === null) throw new ApiError(404, `No such automation: ${id}`, null);
    return settle(found);
  }
  return apiRequest<AutomationDetailOut>(idPath(id));
}

/** `PATCH /engage/automations/{id}`: drafts only; the service refuses anything else. */
export async function updateAutomation(id: string, input: AutomationPatch): Promise<AutomationOut> {
  if (USE_FIXTURES) refuseWithoutService("Editing an automation");
  return apiRequest<AutomationOut>(idPath(id), { method: "PATCH", body: input });
}

/** `DELETE /engage/automations/{id}`: draft or paused only. */
export async function deleteAutomation(id: string): Promise<void> {
  if (USE_FIXTURES) refuseWithoutService("Deleting an automation");
  await apiRequest<void>(idPath(id), { method: "DELETE" });
}

/** `POST /engage/automations/{id}/publish`: validates, freezes a version, goes `active`. A 422
 *  carries the problems; `problemsFrom` reads them off the error. */
export async function publishAutomation(id: string): Promise<AutomationOut> {
  if (USE_FIXTURES) refuseWithoutService("Publishing an automation");
  return apiRequest<AutomationOut>(`${idPath(id)}/publish`, { method: "POST" });
}

/** `POST /engage/automations/{id}/pause`. */
export async function pauseAutomation(id: string): Promise<AutomationOut> {
  if (USE_FIXTURES) refuseWithoutService("Pausing an automation");
  return apiRequest<AutomationOut>(`${idPath(id)}/pause`, { method: "POST" });
}

/** `POST /engage/automations/{id}/resume`. */
export async function resumeAutomation(id: string): Promise<AutomationOut> {
  if (USE_FIXTURES) refuseWithoutService("Resuming an automation");
  return apiRequest<AutomationOut>(`${idPath(id)}/resume`, { method: "POST" });
}

/** `GET /engage/automations/{id}/runs?state&page`. */
export async function listRuns(
  automationId: string,
  options: { state?: RunState; page?: number; size?: number } = {},
): Promise<Page<RunOut>> {
  const { state, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const rows = AUTOMATION_RUNS.filter((r) => r.automation_id === automationId && (state === undefined || r.state === state));
    return settle(fixturePage(rows));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (state !== undefined) query.set("state", state);
  return apiRequest<Page<RunOut>>(`${idPath(automationId)}/runs?${query.toString()}`);
}

/** `GET /engage/automations/runs/{run_id}`. */
export async function getRun(runId: string): Promise<RunDetailOut> {
  if (USE_FIXTURES) {
    const run = AUTOMATION_RUNS.find((r) => r.id === runId);
    if (run === undefined) throw new ApiError(404, `No such run: ${runId}`, null);
    return settle(
      run.state === "failed"
        ? RUN_DETAIL_FAILED
        : {
            ...RUN_DETAIL_FAILED,
            ...run,
            steps: [
              RUN_DETAIL_FAILED.steps[0] as RunStepOut,
              { node_id: "wait_1", type: "wait", state: "waiting", started_at: run.started_at, finished_at: null, attempt: 1, detail: null, message_id: null },
            ],
          },
    );
  }
  return apiRequest<RunDetailOut>(`${BASE}/runs/${encodeURIComponent(runId)}`);
}

/** `POST /engage/automations/runs/{run_id}/retry`: failed runs only. */
export async function retryRun(runId: string): Promise<{ ok: true }> {
  if (USE_FIXTURES) refuseWithoutService("Retrying a run");
  return apiRequest<{ ok: true }>(`${BASE}/runs/${encodeURIComponent(runId)}/retry`, {
    method: "POST",
  });
}

/** `GET /engage/automations/stats?from&to`, dates as `YYYY-MM-DD`. */
export async function getStats(range: { from?: string; to?: string } = {}): Promise<StatsOut> {
  if (USE_FIXTURES) return settle(STATS);
  const query = new URLSearchParams();
  if (range.from !== undefined) query.set("from", range.from);
  if (range.to !== undefined) query.set("to", range.to);
  const suffix = query.toString();
  return apiRequest<StatsOut>(`${BASE}/stats${suffix === "" ? "" : `?${suffix}`}`);
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
  if (USE_FIXTURES) return settle(WHATSAPP_TEMPLATE_OPTIONS);
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

/** Email templates: `GET /engage/email/templates`. */
export async function listEmailTemplateOptions(): Promise<TemplateOption[]> {
  if (USE_FIXTURES) return settle(EMAIL_TEMPLATE_OPTIONS);
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  const page = await apiRequest<Page<EmailTemplateRow>>(
    `/engage/email/templates?${query.toString()}`,
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