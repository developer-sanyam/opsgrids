import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ArrowLeftIcon, ChevronRight, ShieldIcon, SparkIcon, Tabs, useUnsavedChangesWarning } from "@opsgrids/ui";

import {
  createAutomation,
  deleteAutomation,
  getAutomation,
  listTriggers,
  pauseAutomation,
  problemsFrom,
  publishAutomation,
  resumeAutomation,
  updateAutomation,
} from "@/api/automations";
import type { AutomationDetailOut, Graph, GraphNode, TriggerOut, ValidationProblem } from "@/api/automations";
import { failureMessage } from "@/lib/errors";
import { useRouteNotice } from "@/lib/useRouteNotice";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  InlineAlert,
  PageHeading,
  Skeleton,
  TextArea,
  TextField,
  useAnnounce,
} from "@/ui";
import { FlowEditor } from "./FlowEditor";
import {
  addStep,
  blockingProblems,
  buildFlow,
  canMove,
  emptyGraph,
  graphsEqual,
  groupProblems,
  moveStep,
  nodeById,
  NODE_LABEL,
  numberSteps,
  removeStep,
  replaceNode,
  stepsRemovedWith,
  triggerNodes,
  validateGraph,
} from "./graph";
import type { AddableType, InsertPoint, MoveDirection } from "./graph";
import { AutomationStatusTag } from "./Tags";
import { LoadError } from "./LoadError";
import { findRecipe } from "./recipes";
import { RunList } from "./RunList";
import { StepSettings } from "./StepSettings";
import type { TriggerCatalogue } from "./StepSettings";
import { useTemplateOptions } from "./useTemplateOptions";

/**
 * The builder: one screen for a WhatsApp flow and an email flow, because the only thing that
 * differs is the channel chosen on a send step.
 *
 * While an automation is a `draft` it is editable, and *save* and *publish* are two different
 * promises: saving keeps whatever is there, however unfinished; publishing freezes a version and
 * starts it running, and is refused until every step has what it needs. The client checks first
 * (`validateGraph`) so a person is told before they press the button — and the service checks
 * again and has the last word. Its problems are written on the step they name.
 *
 * Once an automation is `active` or `paused` its flow is a record: the service refuses edits to a
 * published version, so the flow is shown read only, beside its runs and the controls that are
 * allowed.
 */

const NO_TRIGGERS: TriggerOut[] = [];

type Busy = "save" | "publish" | "status" | null;

interface Snapshot {
  name: string;
  description: string;
  graph: Graph;
}

function removalBody(steps: number): string {
  const count = steps === 1 ? "the 1 step" : `the ${steps} steps`;
  return `This removes the condition and ${count} on its yes and no paths. This can't be undone once you save.`;
}

function Breadcrumbs({ title }: { title?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-slate-500">
      <Link
        to="/automations"
        className="relative inline-flex items-center gap-1.5 font-medium text-slate-600 hover:text-slate-900 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ArrowLeftIcon className="size-4" />
        All automations
      </Link>
      {title ? (
        <>
          <ChevronRight className="size-3.5 text-slate-400" aria-hidden="true" />
          <span className="font-medium text-slate-700 truncate max-w-md">{title}</span>
        </>
      ) : null}
    </nav>
  );
}

const BackLink = Breadcrumbs;

function BuilderView({ automation, initial }: { automation: AutomationDetailOut | null; initial: Snapshot }) {
  const navigate = useNavigate();
  const announce = useAnnounce();
  const titleRef = useRef<HTMLHeadingElement>(null);
  useRouteNotice(titleRef);
  const nameRef = useRef<HTMLInputElement>(null);

  const [automationId, setAutomationId] = useState<string | null>(automation?.id ?? null);
  const [status, setStatus] = useState(automation?.status ?? "draft");
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [graph, setGraph] = useState<Graph>(initial.graph);
  const [saved, setSaved] = useState<Snapshot>(initial);
  const [selectedId, setSelectedId] = useState<string | null>(triggerNodes(initial.graph)[0]?.id ?? null);
  const [serverProblems, setServerProblems] = useState<ValidationProblem[]>(automation?.validation.problems ?? []);
  const [nameError, setNameError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ id: string; nonce: number } | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<"pause" | "resume" | "delete" | null>(null);
  const [confirmFailure, setConfirmFailure] = useState<string | null>(null);
  const [tab, setTab] = useState<"flow" | "runs">("flow");
  const [discarding, setDiscarding] = useState(false);
  /* A new automation starts empty, so "Choose the event" is not a mistake yet; it is a blank. The
     step-level problems show once the person has touched the flow or tried to save. */
  const [touched, setTouched] = useState(automation !== null);

  const readOnly = status !== "draft";
  const dirty = name !== saved.name || description !== saved.description || !graphsEqual(graph, saved.graph);
  useUnsavedChangesWarning(dirty && !readOnly);

  /* Triggers: the events an automation can start from, and the variables each one carries. */
  const triggerLoader = useCallback(() => listTriggers(), []);
  const triggerState = useAsync(triggerLoader, []);
  const triggers: TriggerCatalogue = {
    status: triggerState.status === "success" ? "ready" : triggerState.status,
    items: triggerState.status === "success" ? triggerState.data : NO_TRIGGERS,
    error: triggerState.status === "error" ? triggerState.error.message : null,
    reload: triggerState.reload,
  };
  const trigger = triggerNodes(graph)[0];
  const eventType = trigger?.type === "trigger" ? trigger.config.event_type : "";
  const variables = triggers.items.find((t) => t.event_type === eventType)?.variables ?? [];
  const triggerLabels = useMemo(
    () => Object.fromEntries(triggers.items.map((t) => [t.event_type, t.label])),
    [triggers.items],
  );

  const channels = graph.nodes.flatMap((n) => (n.type === "send_message" ? [n.config.channel] : []));
  const templates = useTemplateOptions(channels);

  const flow = useMemo(() => buildFlow(graph), [graph]);
  const numbers = useMemo(() => numberSteps(flow), [flow]);
  const localProblems = useMemo(() => validateGraph(graph, templates.catalogue), [graph, templates.catalogue]);
  const allProblems = useMemo(() => [...serverProblems, ...localProblems], [serverProblems, localProblems]);
  const grouped = useMemo(() => groupProblems(allProblems), [allProblems]);
  const groupedServer = useMemo(() => groupProblems(serverProblems), [serverProblems]);
  const blockers = blockingProblems([...grouped.general, ...Object.values(grouped.byNode).flat()]);
  const nameMissing = name.trim() === "";
  const blockerCount = blockers.length + (nameMissing ? 1 : 0);
  const selected = selectedId === null ? undefined : nodeById(graph, selectedId);

  function edit(next: Graph) {
    setGraph(next);
    setTouched(true);
    /* The service's problems described the graph as it was last saved; once it changes they may no
       longer be true, and the live checks above take over until the next save. */
    setServerProblems([]);
    setFailure(null);
  }

  function focusStep(id: string) {
    setFocusRequest((current) => ({ id, nonce: (current?.nonce ?? 0) + 1 }));
  }

  function onAdd(at: InsertPoint, type: AddableType) {
    const result = addStep(graph, at, type);
    edit(result.graph);
    setSelectedId(result.nodeId);
    announce(`${NODE_LABEL[type]} step added.`);
    focusStep(result.nodeId);
  }

  function doRemove(id: string) {
    const node = nodeById(graph, id);
    const number = numbers.get(id);
    edit(removeStep(graph, id));
    const fallback = triggerNodes(graph)[0]?.id ?? null;
    setSelectedId(fallback);
    if (fallback !== null) focusStep(fallback);
    announce(`Step ${number ?? ""}, ${node === undefined ? "" : NODE_LABEL[node.type]}, removed.`);
  }

  function onRemove(id: string) {
    if (stepsRemovedWith(graph, id) > 0) setRemoving(id);
    else doRemove(id);
  }

  function onMove(id: string, direction: MoveDirection) {
    edit(moveStep(graph, id, direction));
    focusStep(id);
    announce(`Step moved ${direction}.`);
  }

  function onNodeChange(node: GraphNode) {
    edit(replaceNode(graph, node));
  }

  /* Save the draft as it stands. Returns the automation's id, or null if the service said no. The
     form keeps everything that was entered either way. */
  async function persist(): Promise<string | null> {
    setTouched(true);
    if (nameMissing) {
      setNameError("Give the automation a name.");
      nameRef.current?.focus();
      return null;
    }
    setNameError(null);
    setFailure(null);
    try {
      const body = { name: name.trim(), description, graph };
      const row = automationId === null ? await createAutomation(body) : await updateAutomation(automationId, body);
      setAutomationId(row.id);
      setSaved({ name: name.trim(), description, graph });
      setServerProblems([]);
      return row.id;
    } catch (caught) {
      const problems = problemsFrom(caught);
      if (problems.length > 0) {
        setServerProblems(problems);
        setFailure("Some steps need fixing before this can be saved. They are marked in the flow. Nothing you entered was lost.");
      } else {
        setFailure(failureMessage(caught));
      }
      return null;
    }
  }

  async function onSaveDraft() {
    if (busy !== null) return;
    setBusy("save");
    const wasNew = automationId === null;
    const id = await persist();
    setBusy(null);
    if (id === null) return;
    if (wasNew) navigate(`/automations/${id}`, { replace: true, state: { notice: "Draft saved." } });
    else announce("Draft saved.");
  }

  async function onPublish() {
    if (busy !== null || blockerCount > 0) return;
    setBusy("publish");
    const wasNew = automationId === null;
    const id = await persist();
    if (id === null) {
      setBusy(null);
      return;
    }
    try {
      await publishAutomation(id);
      navigate("/automations", { state: { notice: `${name.trim()} is now active.` } });
    } catch (caught) {
      setBusy(null);
      const problems = problemsFrom(caught);
      if (wasNew) {
        navigate(`/automations/${id}`, {
          replace: true,
          state: { notice: "Draft saved, but it can't be published yet. Fix the marked steps." },
        });
        return;
      }
      if (problems.length > 0) {
        setServerProblems(problems);
        setFailure("Publishing was refused. The steps that need fixing are marked in the flow.");
      } else {
        setFailure(failureMessage(caught));
      }
    }
  }

  async function onConfirmStatus() {
    if (confirming === null || automationId === null || busy !== null) return;
    setBusy("status");
    setConfirmFailure(null);
    try {
      if (confirming === "pause") {
        await pauseAutomation(automationId);
        setStatus("paused");
        announce(`${name} paused.`);
      } else if (confirming === "resume") {
        await resumeAutomation(automationId);
        setStatus("active");
        announce(`${name} resumed.`);
      } else {
        await deleteAutomation(automationId);
        navigate("/automations", { state: { notice: `${name} deleted.` } });
      }
      setConfirming(null);
    } catch (caught) {
      setConfirmFailure(failureMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const blockedList = blockers.length > 0 || nameMissing;

  return (
    <div className="rise flex flex-col gap-6">
      <Breadcrumbs title={automation === null ? "Create automation" : (saved.name || "Untitled automation")} />
      <PageHeading
        ref={titleRef}
        eyebrow="Automations"
        title={automation === null ? "Create automation" : (saved.name || "Untitled automation")}
        description="Choose what starts it, add the steps, then publish. Saving keeps a draft; nothing runs until you publish."
        actions={
          <div className="flex items-center gap-3">
            <AutomationStatusTag status={status} />
            {status === "active" ? (
              <Button
                variant="secondary"
                size="sm"
                className="flex items-center gap-1.5 rounded-lg border border-slate-300 px-3.5 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs hover:bg-slate-50"
                onClick={() => setConfirming("pause")}
              >
                <span className="font-mono text-xs font-bold leading-none" aria-hidden="true">||</span>
                <span>Pause</span>
              </Button>
            ) : status === "paused" ? (
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex items-center gap-1.5 rounded-lg border border-slate-300 px-3.5 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs hover:bg-slate-50"
                  onClick={() => setConfirming("resume")}
                >
                  <span className="text-xs leading-none" aria-hidden="true">▶</span>
                  <span>Resume</span>
                </Button>
                <Button variant="danger" size="sm" onClick={() => setConfirming("delete")}>
                  Delete
                </Button>
              </div>
            ) : null}
            <button
              type="button"
              aria-label="More options"
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            >
              <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="12" cy="12" r="1" />
                <circle cx="12" cy="5" r="1" />
                <circle cx="12" cy="19" r="1" />
              </svg>
            </button>
          </div>
        }
      />

      {failure !== null ? (
        <InlineAlert tone="danger" title="This wasn't saved">
          {failure}
        </InlineAlert>
      ) : null}

      {readOnly ? (
        <InlineAlert
          tone="info"
          className="rounded-xl border border-blue-200/90 bg-blue-50/70 shadow-2xs"
          title={status === "active" ? "This automation is active" : "This automation is paused"}
        >
          A published version can't be edited. The flow below is what is running.
        </InlineAlert>
      ) : null}

      {automationId !== null ? (
        <Tabs
          label="Automation sections"
          items={[
            { id: "flow", label: "Flow" },
            { id: "runs", label: "Runs" },
          ]}
          value={tab}
          onChange={(id) => setTab(id === "runs" ? "runs" : "flow")}
        />
      ) : null}

      {tab === "runs" && automationId !== null ? (
        <RunList automationId={automationId} published={status !== "draft"} />
      ) : (
        <>
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-12">
            {/* Left Column: Canvas FlowEditor */}
            <div className="flex flex-col gap-4 rounded-3xl border border-slate-200/80 bg-slate-50/50 p-4 sm:p-6 shadow-2xs lg:col-span-7 xl:col-span-8 min-h-[550px]">
              <FlowEditor
                flow={flow}
                selectedId={selectedId}
                numbers={numbers}
                triggerLabels={triggerLabels}
                problemsByNode={touched ? grouped.byNode : groupedServer.byNode}
                readOnly={readOnly}
                focusRequest={focusRequest}
                onSelect={setSelectedId}
                onAdd={onAdd}
                onRemove={onRemove}
                onMove={onMove}
                canMove={(id, direction) => canMove(graph, id, direction)}
              />
            </div>

            {/* Right Column: Settings and Safeguards */}
            <div className="flex flex-col gap-5 lg:col-span-5 xl:col-span-4">
              {/* Card 1: Automation details */}
              <Card className="flex flex-col gap-4 p-5 rounded-2xl border-slate-200/90 shadow-2xs bg-white">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div className="flex size-8 items-center justify-center rounded-full bg-blue-50 text-blue-600 border border-blue-100">
                      <SparkIcon className="size-4" aria-hidden />
                    </div>
                    <h2 className="text-base font-semibold text-slate-900">Automation details</h2>
                  </div>
                  {readOnly ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      className="text-xs h-7 px-2.5 rounded-lg border-slate-200 text-slate-700"
                    >
                      Edit
                    </Button>
                  ) : null}
                </div>
                <TextField
                  ref={nameRef}
                  label="Name"
                  required
                  value={name}
                  disabled={readOnly}
                  onChange={(event) => {
                    setName(event.target.value);
                    setNameError(null);
                  }}
                  {...(nameError !== null ? { error: nameError } : {})}
                />
                <TextArea
                  label="Description (optional)"
                  optional
                  rows={3}
                  value={description}
                  disabled={readOnly}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </Card>

              {/* Card 2: Step settings & Safeguards */}
              <Card className="flex flex-col gap-5 p-5 rounded-2xl border-slate-200/90 shadow-2xs bg-white">
                {selected !== undefined ? (
                  <StepSettings
                    key={selected.id}
                    node={selected}
                    readOnly={readOnly}
                    triggers={triggers}
                    variables={variables}
                    templates={templates}
                    onChange={onNodeChange}
                  />
                ) : (
                  <p className="text-sm text-slate-500">Select a step to change its settings.</p>
                )}

                <div className="flex items-start gap-3 rounded-xl border border-slate-200/80 bg-slate-50/60 p-3.5">
                  <ShieldIcon className="size-5 shrink-0 text-slate-500 mt-0.5" aria-hidden />
                  <div className="flex flex-col gap-1">
                    <h3 className="text-xs font-semibold text-slate-900">Safeguards</h3>
                    <p className="text-xs text-slate-600 leading-relaxed">
                      Messages only go to contacts who have consented. Each event starts at most one run.
                      Temporary failures retry automatically; permanent ones mark the run failed so you can review and retry it.
                    </p>
                  </div>
                </div>
              </Card>

              {/* Card 3: Publish Block (in draft mode) */}
              {readOnly ? null : (
                <Card className="flex flex-col gap-4 p-5 rounded-2xl border-slate-200/90 shadow-2xs bg-white">
                  {blockedList ? (
                    <>
                      <InlineAlert
                        tone="warning"
                        title={`Fix ${blockerCount} ${blockerCount === 1 ? "thing" : "things"} to publish`}
                      >
                        Publish is blocked until every step has what it needs. Saving a draft is always allowed.
                      </InlineAlert>
                      <ul id="publish-blockers" className="flex flex-col gap-1 text-sm">
                        {nameMissing ? (
                          <li>
                            <button
                              type="button"
                              className="relative hit-44 text-left text-accent-ink underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                              onClick={() => nameRef.current?.focus()}
                            >
                              Give the automation a name.
                            </button>
                          </li>
                        ) : null}
                        {grouped.general.map((p) => (
                          <li key={`${p.code}-${p.message}`}>{p.message}</li>
                        ))}
                        {Object.entries(grouped.byNode).flatMap(([id, list]) =>
                          list
                            .filter((p) => blockers.includes(p))
                            .map((p) => (
                              <li key={`${id}-${p.code}-${p.message}`}>
                                <button
                                  type="button"
                                  className="relative hit-44 text-left text-accent-ink underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                                  onClick={() => {
                                    setSelectedId(id);
                                    focusStep(id);
                                  }}
                                >
                                  Step {numbers.get(id) ?? "?"}: {p.message}
                                </button>
                              </li>
                            )),
                        )}
                      </ul>
                    </>
                  ) : (
                    <InlineAlert tone="success" title="Ready to publish">
                      Every step has what it needs. Publishing starts this automation for new events.
                    </InlineAlert>
                  )}
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <Button variant="secondary" onClick={() => (dirty ? setDiscarding(true) : navigate("/automations"))}>
                      Cancel
                    </Button>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="secondary"
                        loading={busy === "save"}
                        disabled={busy !== null}
                        onClick={() => void onSaveDraft()}
                      >
                        {busy === "save" ? "Saving…" : "Save draft"}
                      </Button>
                      <Button
                        loading={busy === "publish"}
                        disabled={busy !== null || blockerCount > 0}
                        aria-describedby={blockedList ? "publish-blockers" : undefined}
                        onClick={() => void onPublish()}
                      >
                        {busy === "publish" ? "Publishing…" : "Publish"}
                      </Button>
                    </div>
                  </div>
                </Card>
              )}
            </div>
          </div>
        </>
      )}

      <ConfirmDialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        onConfirm={() => navigate("/automations")}
        title="Discard your changes?"
        body="This automation has changes that haven't been saved. Leaving now loses them."
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
      />

      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          if (removing !== null) doRemove(removing);
          setRemoving(null);
        }}
        title="Delete this condition?"
        body={removalBody(removing === null ? 0 : stepsRemovedWith(graph, removing))}
        confirmLabel="Delete condition"
      />

      <ConfirmDialog
        open={confirming !== null}
        onClose={() => {
          if (busy === null) {
            setConfirming(null);
            setConfirmFailure(null);
          }
        }}
        onConfirm={() => void onConfirmStatus()}
        title={
          confirming === "pause"
            ? "Pause this automation?"
            : confirming === "resume"
              ? "Resume this automation?"
              : "Delete this automation?"
        }
        body={
          confirming === "pause"
            ? "It stops starting new runs. Runs already in progress keep going."
            : confirming === "resume"
              ? "It starts running again for new events."
              : "It is removed. Its past runs are kept for the record. This can't be undone."
        }
        confirmLabel={confirming === "pause" ? "Pause automation" : confirming === "resume" ? "Resume automation" : "Delete automation"}
        pendingLabel="Working…"
        tone={confirming === "delete" ? "danger" : "primary"}
        pending={busy === "status"}
        {...(confirmFailure !== null ? { error: confirmFailure } : {})}
      />
    </div>
  );
}

function LoadingBuilder() {
  return (
    <Card className="flex flex-col gap-4 p-6" aria-busy="true">
      <span className="sr-only" role="status">
        Loading automation
      </span>
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-64 w-full rounded-tile" />
    </Card>
  );
}

function ExistingBuilder({ automationId }: { automationId: string }) {
  const loader = useCallback(() => getAutomation(automationId), [automationId]);
  const state = useAsync(loader, [automationId]);
  if (state.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <BackLink />
        <LoadingBuilder />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <BackLink />
        <LoadError error={state.error} title="Couldn't load this automation" onRetry={state.reload} />
      </div>
    );
  }
  const detail = state.data;
  return (
    <BuilderView
      key={detail.id}
      automation={detail}
      initial={{ name: detail.name, description: detail.description, graph: detail.graph }}
    />
  );
}

function NewBuilder() {
  const [params] = useSearchParams();
  const recipe = findRecipe(params.get("recipe"));
  const initial = useMemo<Snapshot>(
    () => ({
      name: recipe?.name ?? "",
      description: recipe?.description ?? "",
      graph: recipe?.build() ?? emptyGraph(),
    }),
    [recipe],
  );
  return <BuilderView key={recipe?.id ?? "blank"} automation={null} initial={initial} />;
}

export function BuilderPage() {
  const { automationId } = useParams<{ automationId: string }>();
  return (
    <Announcer>
      {automationId === undefined ? <NewBuilder /> : <ExistingBuilder automationId={automationId} />}
    </Announcer>
  );
}
