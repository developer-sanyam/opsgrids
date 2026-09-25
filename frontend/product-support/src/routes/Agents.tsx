import { useMemo, useRef, useState } from "react";

import {
  createAgent,
  deleteAgent,
  getSlaPolicy,
  listAgents,
  setSlaPolicy,
  updateAgent,
} from "@/api/client";
import type { Agent, AgentIn, AgentPatch, Priority, SlaPolicy, SlaPolicyIn, SlaTarget } from "@/api/types";
import {
  ApiError,
  Can,
  problemMessage,
  useAsync,
  useCan,
  useMutation,
} from "@opsgrids/client";
import { GaugeIcon, cn } from "@opsgrids/ui";
import { formatMinutes, priorityLabel } from "@/lib/format";
import { errorProp } from "@/lib/optionalProp";
import {
  Avatar,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import {
  ConfirmDialog,
  FormDialog,
  RowActions,
  Select,
  Switch,
  TextArea,
  TextField,
  useFormDirty,
} from "@opsgrids/ui";

const ROLE_LABEL: Record<Agent["role"], string> = {
  agent: "Agent",
  senior_agent: "Senior Agent",
  team_lead: "Team Lead",
};

const ROLE_OPTIONS: { value: Agent["role"]; label: string }[] = [
  { value: "agent", label: "Agent" },
  { value: "senior_agent", label: "Senior Agent" },
  { value: "team_lead", label: "Team Lead" },
];

const PRIORITY_ORDER: Priority[] = ["urgent", "high", "normal", "low"];

const PRIORITY_DOT: Record<Priority, string> = {
  urgent: "bg-danger",
  high: "bg-late",
  normal: "bg-info",
  low: "bg-faint",
};

/** Online / Away, labelled so the state never rests on the dot's colour. */
function PresencePill({ online }: { online: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
        online ? "bg-present-soft text-present" : "bg-ground text-muted",
      )}
    >
      <span
        className={cn("size-1.5 rounded-full", online ? "bg-present" : "bg-faint")}
        aria-hidden
      />
      {online ? "Online" : "Away"}
    </span>
  );
}

/** A compact CSAT meter — a labelled percentage with a proportional track. */
function CsatMeter({ pct }: { pct: number }) {
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-ground sm:block">
        <div className="h-full rounded-full bg-present" style={{ width: `${pct}%` }} />
      </div>
      <span className="tnum font-semibold text-ink">{pct}%</span>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
      <span className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</span>
      <span className="display text-2xl font-extrabold tnum">{value}</span>
    </div>
  );
}

/* --------------------------------------------------------------------- agent form */

function AgentForm({
  agent,
  onClose,
  onSaved,
}: {
  agent: Agent | null;
  onClose: () => void;
  onSaved: (agent: Agent) => void;
}) {
  const [name, setName] = useState(agent?.name ?? "");
  const [email, setEmail] = useState(agent?.email ?? "");
  const [role, setRole] = useState<Agent["role"]>(agent?.role ?? "agent");
  const [team, setTeam] = useState(agent?.team ?? "Tier 1");
  const [online, setOnline] = useState(agent?.online ?? false);

  const fields = { name, email, role, team, online };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: AgentIn | AgentPatch) =>
      agent ? updateAgent(agent.id, input as AgentPatch) : createAgent(input as AgentIn),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  const topLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0
      ? problemMessage(save.error)
      : undefined;
  const requestId =
    topLevelError !== undefined ? (save.error as ApiError).problem?.request_id : undefined;

  function submit() {
    setNameError(undefined);
    setEmailError(undefined);
    const trimmedName = name.trim();
    const trimmedEmail = email.trim();
    if (trimmedName.length < 1 || trimmedName.length > 160) {
      setNameError("Give the agent a name, up to 160 characters.");
      nameRef.current?.focus();
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      setEmailError("Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    void save.mutate({ name: trimmedName, email: trimmedEmail, role, team: team.trim(), online });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={agent ? `Edit ${agent.name}` : "Add agent"}
      submitLabel={agent ? "Save changes" : "Add agent"}
      pendingLabel={agent ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      dirty={dirty}
      {...errorProp(topLevelError)}
      {...(requestId !== undefined ? { reference: requestId } : {})}
    >
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...errorProp(nameError ?? save.fieldErrors.name)}
      />
      <TextField
        ref={emailRef}
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
        {...errorProp(emailError ?? save.fieldErrors.email)}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Role"
          value={role}
          onChange={(e) => setRole(e.target.value as Agent["role"])}
          options={ROLE_OPTIONS}
          {...errorProp(save.fieldErrors.role)}
        />
        <TextField
          label="Team"
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          {...errorProp(save.fieldErrors.team)}
        />
      </div>
      <Switch
        checked={online}
        onChange={setOnline}
        label="Online"
        description="Whether this agent currently shows as available in the queue."
      />
    </FormDialog>
  );
}

/* --------------------------------------------------------------------- SLA policy form */

function emptyTargets(): Record<Priority, { first_response_min: string; resolution_min: string }> {
  return {
    urgent: { first_response_min: "", resolution_min: "" },
    high: { first_response_min: "", resolution_min: "" },
    normal: { first_response_min: "", resolution_min: "" },
    low: { first_response_min: "", resolution_min: "" },
  };
}

function targetsFrom(policy: SlaPolicy): Record<Priority, { first_response_min: string; resolution_min: string }> {
  const out = emptyTargets();
  for (const t of policy.targets) {
    out[t.priority] = {
      first_response_min: String(t.first_response_min),
      resolution_min: String(t.resolution_min),
    };
  }
  return out;
}

function SlaPolicyForm({
  policy,
  onClose,
  onSaved,
}: {
  policy: SlaPolicy | null;
  onClose: () => void;
  onSaved: (policy: SlaPolicy) => void;
}) {
  const [name, setName] = useState(policy?.name ?? "");
  const [businessHours, setBusinessHours] = useState(policy?.business_hours ?? "");
  const [coverage, setCoverage] = useState(policy?.coverage ?? "");
  const [targets, setTargets] = useState(policy ? targetsFrom(policy) : emptyTargets());

  const fields = { name, businessHours, coverage, targets };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [targetError, setTargetError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: SlaPolicyIn) => setSlaPolicy(input), {
    onSuccess: (saved) => { onClose(); onSaved(saved); },
  });

  const topLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0
      ? problemMessage(save.error)
      : undefined;
  const requestId =
    topLevelError !== undefined ? (save.error as ApiError).problem?.request_id : undefined;

  function setTarget(priority: Priority, field: "first_response_min" | "resolution_min", value: string) {
    setTargets((prev) => ({ ...prev, [priority]: { ...prev[priority], [field]: value } }));
  }

  function submit() {
    setNameError(undefined);
    setTargetError(undefined);
    const trimmedName = name.trim();
    if (trimmedName.length < 1 || trimmedName.length > 160) {
      setNameError("Give the policy a name, up to 160 characters.");
      nameRef.current?.focus();
      return;
    }
    const parsed: SlaTarget[] = [];
    for (const priority of PRIORITY_ORDER) {
      const row = targets[priority];
      const firstResponse = Number(row.first_response_min);
      const resolution = Number(row.resolution_min);
      if (!Number.isInteger(firstResponse) || firstResponse < 1 || !Number.isInteger(resolution) || resolution < 1) {
        setTargetError(`Enter whole-minute targets of at least 1 for ${priorityLabel(priority)}.`);
        return;
      }
      parsed.push({ priority, first_response_min: firstResponse, resolution_min: resolution });
    }
    void save.mutate({
      name: trimmedName,
      business_hours: businessHours.trim(),
      coverage: coverage.trim(),
      targets: parsed,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={policy ? "Edit SLA policy" : "Set up your SLA policy"}
      submitLabel={policy ? "Save changes" : "Create policy"}
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={nameRef}
      size="lg"
      dirty={dirty}
      {...errorProp(topLevelError)}
      {...(requestId !== undefined ? { reference: requestId } : {})}
    >
      <TextField
        ref={nameRef}
        label="Policy name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...errorProp(nameError ?? save.fieldErrors.name)}
      />
      <TextField
        label="Business hours"
        optional
        placeholder="Mon–Fri, 08:00–20:00 IST"
        value={businessHours}
        onChange={(e) => setBusinessHours(e.target.value)}
        {...errorProp(save.fieldErrors.business_hours)}
      />
      <TextArea
        label="Coverage"
        optional
        rows={2}
        placeholder="Urgent tickets are covered 24×7; all other priorities follow business hours."
        value={coverage}
        onChange={(e) => setCoverage(e.target.value)}
        {...errorProp(save.fieldErrors.coverage)}
      />
      <div className="flex flex-col gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">
          Targets by priority, in minutes
        </span>
        {PRIORITY_ORDER.map((priority) => (
          <div key={priority} className="grid grid-cols-[auto_1fr_1fr] items-end gap-3">
            <span className="inline-flex items-center gap-2 pb-2.5 text-sm font-semibold text-ink">
              <span className={cn("size-2 rounded-full", PRIORITY_DOT[priority])} aria-hidden />
              {priorityLabel(priority)}
            </span>
            <TextField
              label="First response"
              type="number"
              min={1}
              inputMode="numeric"
              value={targets[priority].first_response_min}
              onChange={(e) => setTarget(priority, "first_response_min", e.target.value)}
              required
            />
            <TextField
              label="Resolution"
              type="number"
              min={1}
              inputMode="numeric"
              value={targets[priority].resolution_min}
              onChange={(e) => setTarget(priority, "resolution_min", e.target.value)}
              required
            />
          </div>
        ))}
        {targetError ? <p className="text-xs font-medium text-negative">{targetError}</p> : null}
      </div>
    </FormDialog>
  );
}

/* --------------------------------------------------------------------- screen */

export function AgentsPage() {
  const canManageAgents = useCan("support.desk.agent.write", "ORG");
  const canManagePolicy = useCan("support.desk.sla_policy.write", "ORG");

  const agentsState = useAsync(listAgents, []);
  const policyState = useAsync(getSlaPolicy, []);

  const [creatingAgent, setCreatingAgent] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [deletingAgent, setDeletingAgent] = useState<Agent | null>(null);
  const [editingPolicy, setEditingPolicy] = useState(false);

  const remove = useMutation((target: Agent) => deleteAgent(target.id), {
    onSuccess: () => {
      setDeletingAgent(null);
      agentsState.reload();
    },
  });

  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : []),
    [agentsState],
  );

  const summary = useMemo(() => {
    if (agents.length === 0) return null;
    const load = agents.reduce((s, a) => s + a.open_load, 0);
    const csat = Math.round(agents.reduce((s, a) => s + a.csat_pct, 0) / agents.length);
    const online = agents.filter((a) => a.online).length;
    const solved = agents.reduce((s, a) => s + a.solved_7d, 0);
    return { load, csat, online, solved };
  }, [agents]);

  const policyMissing = policyState.status === "error" && policyState.error instanceof ApiError && policyState.error.status === 404;
  const policy = policyState.status === "success" ? policyState.data : null;

  const columns: Column<Agent>[] = useMemo(
    () => [
      {
        key: "agent",
        header: "Agent",
        render: (a) => (
          <div className="flex items-center gap-3">
            <Avatar name={a.name} size="md" />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-semibold text-ink">{a.name}</span>
              <span className="truncate text-xs text-muted">
                {ROLE_LABEL[a.role]} · {a.team}
              </span>
            </div>
          </div>
        ),
      },
      {
        key: "presence",
        header: "Status",
        headerClassName: "hidden sm:table-cell",
        cellClassName: "hidden sm:table-cell",
        render: (a) => <PresencePill online={a.online} />,
      },
      {
        key: "load",
        header: "Open load",
        align: "right",
        render: (a) => <span className="tnum font-semibold text-ink">{a.open_load}</span>,
      },
      {
        key: "frt",
        header: "Avg response",
        align: "right",
        headerClassName: "hidden md:table-cell",
        cellClassName: "hidden md:table-cell",
        render: (a) => (
          <span className="tnum text-ink-soft">{formatMinutes(a.avg_first_response_min)}</span>
        ),
      },
      {
        key: "solved",
        header: "Solved · 7d",
        align: "right",
        headerClassName: "hidden lg:table-cell",
        cellClassName: "hidden lg:table-cell",
        render: (a) => <span className="tnum text-ink-soft">{a.solved_7d}</span>,
      },
      {
        key: "csat",
        header: "CSAT",
        align: "right",
        render: (a) => <CsatMeter pct={a.csat_pct} />,
      },
      {
        key: "actions",
        header: "",
        align: "right",
        render: (a) => (
          <RowActions
            label={a.name}
            actions={[
              { label: "Edit", onSelect: () => setEditingAgent(a), hidden: !canManageAgents },
              {
                label: "Remove",
                tone: "danger",
                onSelect: () => setDeletingAgent(a),
                hidden: !canManageAgents,
              },
            ]}
          />
        ),
      },
    ],
    [canManageAgents],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Support"
        title="Agents & SLA"
        actions={
          <div className="flex items-center gap-2">
            <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
              <GaugeIcon className="size-4 text-muted" />
              <span className="tnum">{agents.length}</span>
              <span className="font-medium text-muted">agents</span>
            </span>
            <Can permission="support.desk.agent.write" scope="ORG">
              <Button onClick={() => setCreatingAgent(true)}>Add agent</Button>
            </Can>
          </div>
        }
      />

      {/* Summary tiles */}
      {summary ? (
        <section aria-label="Team summary" className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile label="Online now" value={`${summary.online}/${agents.length}`} />
          <StatTile label="Open load" value={String(summary.load)} />
          <StatTile label="Avg CSAT" value={`${summary.csat}%`} />
          <StatTile label="Solved · 7d" value={String(summary.solved)} />
        </section>
      ) : null}

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-3">
        {/* Agents table */}
        <Card className="p-2 sm:p-4 xl:col-span-2">
          <div className="px-3 pt-3 sm:px-2">
            <SectionHeader title="Agents" subtitle="Load and quality over the last 7 days" />
          </div>
          {agentsState.status === "loading" ? (
            <div className="flex flex-col gap-2 p-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {agentsState.status === "error" ? (
            <ErrorState
              message={agentsState.error.message}
              action={<Button onClick={agentsState.reload}>Try again</Button>}
            />
          ) : null}
          {agentsState.status === "success" ? (
            <div className="mt-2">
              <DataTable
                columns={columns}
                rows={agents}
                rowKey={(a) => a.id}
                caption="Support agents and their current load and quality"
                emptyState={
                  <EmptyState
                    icon={<GaugeIcon className="size-6" />}
                    title="No agents yet"
                    description="A real tenant starts with an empty desk. Add the first agent so tickets can be assigned."
                    action={
                      canManageAgents ? (
                        <Button onClick={() => setCreatingAgent(true)}>Add agent</Button>
                      ) : null
                    }
                  />
                }
              />
            </div>
          ) : null}
        </Card>

        {/* SLA policy */}
        <Card className="flex flex-col gap-5 p-6">
          {policyState.status === "loading" ? (
            <Skeleton className="h-72 w-full rounded-tile" />
          ) : null}
          {policyState.status === "error" && !policyMissing ? (
            <ErrorState
              message={policyState.error.message}
              action={<Button onClick={policyState.reload}>Try again</Button>}
            />
          ) : null}
          {policyMissing ? (
            <EmptyState
              icon={<GaugeIcon className="size-6" />}
              title="No SLA policy yet"
              description="Set one up so tickets carry first-response and resolution targets."
              action={
                canManagePolicy ? (
                  <Button onClick={() => setEditingPolicy(true)}>Set up SLA policy</Button>
                ) : null
              }
            />
          ) : null}
          {policy ? (
            <>
              <SectionHeader
                title="SLA policy"
                subtitle={policy.name}
                action={
                  <Can permission="support.desk.sla_policy.write" scope="ORG">
                    <Button variant="outline" size="sm" onClick={() => setEditingPolicy(true)}>
                      Edit
                    </Button>
                  </Can>
                }
              />
              <dl className="flex flex-col gap-3 text-sm">
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Business hours
                  </dt>
                  <dd className="font-semibold text-ink">{policy.business_hours || "—"}</dd>
                </div>
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Coverage
                  </dt>
                  <dd className="text-ink-soft leading-relaxed">{policy.coverage || "—"}</dd>
                </div>
              </dl>

              <div className="flex flex-col gap-2 border-t border-line pt-4">
                <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                  Targets by priority
                </span>
                <table className="w-full text-sm">
                  <caption className="sr-only">SLA targets by priority</caption>
                  <thead>
                    <tr className="text-xs font-semibold uppercase tracking-wide text-faint">
                      <th scope="col" className="py-2 text-left font-semibold">Priority</th>
                      <th scope="col" className="py-2 text-right font-semibold">1st reply</th>
                      <th scope="col" className="py-2 text-right font-semibold">Resolve</th>
                    </tr>
                  </thead>
                  <tbody>
                    {policy.targets.map((t) => (
                      <tr key={t.priority} className="border-t border-line/70">
                        <td className="py-2.5">
                          <span className="inline-flex items-center gap-2 font-semibold text-ink">
                            <span
                              className={cn("size-2 rounded-full", PRIORITY_DOT[t.priority])}
                              aria-hidden
                            />
                            {priorityLabel(t.priority)}
                          </span>
                        </td>
                        <td className="py-2.5 text-right tnum text-ink-soft">
                          {formatMinutes(t.first_response_min)}
                        </td>
                        <td className="py-2.5 text-right tnum text-ink-soft">
                          {formatMinutes(t.resolution_min)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Card>
      </div>

      {creatingAgent ? (
        <AgentForm
          agent={null}
          onClose={() => setCreatingAgent(false)}
          onSaved={() => agentsState.reload()}
        />
      ) : null}
      {editingAgent ? (
        <AgentForm
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onSaved={() => agentsState.reload()}
        />
      ) : null}
      {editingPolicy ? (
        <SlaPolicyForm
          policy={policy}
          onClose={() => setEditingPolicy(false)}
          onSaved={() => policyState.reload()}
        />
      ) : null}

      <ConfirmDialog
        open={deletingAgent !== null}
        onClose={() => { setDeletingAgent(null); remove.reset(); }}
        onConfirm={() => deletingAgent && void remove.mutate(deletingAgent)}
        title="Remove this agent?"
        body={`${deletingAgent?.name} will no longer be assignable, and every ticket currently assigned to them is unassigned. This can't be undone.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
