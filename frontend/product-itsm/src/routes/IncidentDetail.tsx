import { useCallback, useEffect, useState } from "react";
import type { ComponentType, FormEvent, ReactNode, SVGProps } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  addWorklog,
  changeIncidentStatus,
  deleteIncident,
  getIncident,
  updateIncident,
} from "@/api/client";
import type { Incident, IncidentStatus, Priority, WorklogEntry } from "@/api/types";
import {
  formatDateTime,
  formatSlaClock,
  incidentCategoryLabel,
  relativeTime,
} from "@/lib/format";
import { INCIDENT_TRANSITIONS, transitionLabel } from "@/lib/incidentLifecycle";
import { Can, problemMessage, useAsync, useAuth, useCan, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Card,
  ErrorState,
  IncidentStatusPill,
  PriorityPill,
  SectionHeader,
  Skeleton,
  SlaPill,
} from "@/ui";
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  BoxIcon,
  Button,
  CheckCircleIcon,
  ClockIcon,
  ConfirmDialog,
  InlineAlert,
  MessageIcon,
  Select,
  ShieldCheckIcon,
  TextArea,
  TextField,
  UserIcon,
  useToast,
} from "@opsgrids/ui";
import { EditIncidentDialog } from "@/forms/EditIncidentDialog";
import { ResolveIncidentDialog } from "@/forms/ResolveIncidentDialog";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

const CAN_WRITE = "itsm.service.incident.write";

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "P1", label: "P1 · Critical" },
  { value: "P2", label: "P2 · High" },
  { value: "P3", label: "P3 · Moderate" },
  { value: "P4", label: "P4 · Low" },
];

/** Worklog kind → glyph + tint, so the thread scans as a timeline of typed events. */
const KIND_META: Record<WorklogEntry["kind"], { tone: string; icon: IconType }> = {
  note: { tone: "bg-ground text-muted", icon: MessageIcon },
  assignment: { tone: "bg-accent-soft text-accent-ink", icon: UserIcon },
  status: { tone: "bg-info-soft text-info", icon: ClockIcon },
  sla: { tone: "bg-danger-soft text-danger", icon: ShieldCheckIcon },
  resolution: { tone: "bg-present-soft text-present", icon: CheckCircleIcon },
};

function WorklogThread({ entries }: { entries: WorklogEntry[] }) {
  const ordered = [...entries].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <ol className="flex flex-col">
      {ordered.map((entry, i) => {
        const meta = KIND_META[entry.kind];
        const Icon = meta.icon;
        const last = i === ordered.length - 1;
        return (
          <li key={entry.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className={`grid size-9 shrink-0 place-items-center rounded-full ${meta.tone}`}>
                <Icon className="size-[18px]" />
              </span>
              {!last ? <span className="w-px flex-1 bg-line" aria-hidden /> : null}
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5 pb-6">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm font-bold text-ink">{entry.author}</span>
                <span className="tnum text-xs text-faint" title={formatDateTime(entry.at)}>
                  {relativeTime(entry.at)}
                </span>
              </div>
              <p className="text-sm text-ink-soft leading-relaxed whitespace-pre-line">
                {entry.body}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PropertyRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
      <span className="text-sm text-muted">{label}</span>
      <div className="w-full text-sm font-semibold text-ink sm:w-auto sm:text-right">{children}</div>
    </div>
  );
}

function PriorityControl({
  incident,
  canWriteOrg,
  onChanged,
}: {
  incident: Incident;
  canWriteOrg: boolean;
  onChanged: (incident: Incident) => void;
}) {
  const toast = useToast();
  const change = useMutation((priority: Priority) => updateIncident(incident.id, { priority }), {
    onSuccess: (updated) => {
      onChanged(updated);
      toast.success(`Priority changed to ${updated.priority}.`);
    },
  });

  if (!canWriteOrg) return <PriorityPill priority={incident.priority} />;

  return (
    <div className="flex w-full flex-col gap-1.5 sm:w-40 sm:items-end">
      <Select
        label="Priority"
        hideLabel
        containerClassName="w-full"
        value={incident.priority}
        onChange={(e) => void change.mutate(e.target.value as Priority)}
        disabled={change.pending}
        options={PRIORITY_OPTIONS}
      />
      {change.error ? (
        <InlineAlert tone="danger" title={problemMessage(change.error)} className="w-full" />
      ) : null}
    </div>
  );
}

function AssigneeControl({
  incident,
  canWriteOrg,
  onChanged,
}: {
  incident: Incident;
  canWriteOrg: boolean;
  onChanged: (incident: Incident) => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState(incident.assignee);
  const change = useMutation((assignee: string) => updateIncident(incident.id, { assignee }), {
    onSuccess: (updated) => {
      onChanged(updated);
      toast.success(updated.assignee ? `Assigned to ${updated.assignee}.` : "Unassigned.");
    },
  });

  if (!canWriteOrg) {
    return (
      <span className="inline-flex items-center gap-2">
        <Avatar name={incident.assignee || "Unassigned"} size="sm" />
        {incident.assignee || "Unassigned"}
      </span>
    );
  }

  return (
    <form
      className="flex w-full flex-col gap-1.5 sm:w-44 sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim() !== incident.assignee) void change.mutate(value.trim());
      }}
    >
      <TextField
        label="Assignee"
        hideLabel
        containerClassName="w-full"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => {
          if (value.trim() !== incident.assignee) void change.mutate(value.trim());
        }}
        disabled={change.pending}
        maxLength={120}
        placeholder="Unassigned"
      />
      {change.error ? (
        <InlineAlert tone="danger" title={problemMessage(change.error)} className="w-full" />
      ) : null}
    </form>
  );
}

function StatusActions({
  incident,
  onChanged,
  onResolveRequested,
}: {
  incident: Incident;
  onChanged: (incident: Incident) => void;
  onResolveRequested: () => void;
}) {
  const toast = useToast();
  const targets = INCIDENT_TRANSITIONS[incident.status];
  const change = useMutation(
    (status: IncidentStatus) => changeIncidentStatus(incident.id, { status }),
    {
      onSuccess: (updated) => {
        onChanged(updated);
        toast.success(`Status changed to ${transitionLabel(incident.status, updated.status)}.`);
      },
    },
  );

  if (targets.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {targets.map((target) =>
        target === "resolved" ? (
          <Button key={target} variant="outline" onClick={onResolveRequested}>
            {transitionLabel(incident.status, target)}
          </Button>
        ) : (
          <Button
            key={target}
            variant="outline"
            loading={change.pending}
            onClick={() => void change.mutate(target)}
          >
            {transitionLabel(incident.status, target)}
          </Button>
        ),
      )}
      {change.error ? <InlineAlert tone="danger" title={problemMessage(change.error)} /> : null}
    </div>
  );
}

function WorklogComposer({
  incident,
  canWrite,
  authorName,
  onPosted,
}: {
  incident: Incident;
  canWrite: boolean;
  authorName: string | null;
  onPosted: (incident: Incident) => void;
}) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [bodyError, setBodyError] = useState<string | undefined>(undefined);

  const post = useMutation((text: string) => addWorklog(incident.id, { author: authorName ?? "", body: text }), {
    onSuccess: (updated) => {
      setBody("");
      setBodyError(undefined);
      onPosted(updated);
      toast.success("Note added.");
    },
  });

  if (!canWrite) return null;

  function submit(e: FormEvent) {
    e.preventDefault();
    setBodyError(undefined);
    const trimmed = body.trim();
    if (trimmed.length < 1) {
      setBodyError("Write something before posting.");
      return;
    }
    void post.mutate(trimmed);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 border-t border-line pt-5">
      {authorName === null ? (
        <InlineAlert tone="warning" title="Your account has no display name set.">
          Ask an admin to add one before you can post — the thread needs a name to attribute the
          note to.
        </InlineAlert>
      ) : null}
      <TextArea
        label="Add a note"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={8000}
        placeholder={authorName ? `Add a note as ${authorName}…` : "Add a note…"}
        error={bodyError}
        disabled={authorName === null || post.pending}
      />
      <Button
        type="submit"
        size="sm"
        loading={post.pending}
        disabled={authorName === null || body.trim().length === 0}
        className="ml-auto"
      >
        {post.pending ? "Posting…" : "Add note"}
      </Button>
      {post.error ? <InlineAlert tone="danger" title={problemMessage(post.error)} /> : null}
    </form>
  );
}

export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const canWrite = useCan(CAN_WRITE, "OWN");
  const canWriteOrg = useCan(CAN_WRITE, "ORG");

  const loader = useCallback(() => getIncident(id), [id]);
  const state = useAsync(loader, [id]);
  const [override, setOverride] = useState<Incident | null>(null);
  useEffect(() => {
    setOverride(null);
  }, [id]);
  const incident = override ?? (state.status === "success" ? state.data : null);

  const [editing, setEditing] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const remove = useMutation<void, void>(() => deleteIncident(incident?.id ?? ""), {
    onSuccess: () => {
      toast.success(`${incident?.id} withdrawn.`);
      navigate("/incidents");
    },
  });

  return (
    <div className="rise flex flex-col gap-5">
      <button
        type="button"
        onClick={() => navigate("/incidents")}
        className="hit-44 relative inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to incidents
      </button>

      {state.status === "loading" && !incident ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Card className="p-6 xl:col-span-2">
            <Skeleton label="Loading incident" className="h-8 w-2/3" />
            <Skeleton className="mt-4 h-40 w-full rounded-tile" />
          </Card>
          <Card className="p-6">
            <Skeleton className="h-40 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {state.status === "error" && !incident ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load that incident"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        </Card>
      ) : null}

      {incident ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="tnum text-sm font-semibold text-muted">{incident.id}</span>
                <PriorityPill priority={incident.priority} />
                <IncidentStatusPill status={incident.status} />
                <SlaPill state={incident.sla_state} />
              </div>
              <h1 className="display text-2xl sm:text-3xl font-extrabold leading-tight">
                {incident.summary}
              </h1>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Can permission={CAN_WRITE} scope="OWN">
                <Button variant="outline" onClick={() => setEditing(true)}>
                  Edit
                </Button>
              </Can>
              <Can permission={CAN_WRITE} scope="ORG">
                <StatusActions
                  incident={incident}
                  onChanged={setOverride}
                  onResolveRequested={() => setResolving(true)}
                />
              </Can>
              <Can permission={CAN_WRITE} scope="ORG">
                <Button variant="danger" onClick={() => setDeleting(true)}>
                  Withdraw
                </Button>
              </Can>
            </div>
          </header>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
            {/* Left — description + activity thread */}
            <div className="flex flex-col gap-5 xl:col-span-2">
              <Card className="flex flex-col gap-3 p-6">
                <SectionHeader title="Description" />
                <p className="text-sm text-ink-soft leading-relaxed whitespace-pre-line">
                  {incident.description || "No description was given."}
                </p>
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <SectionHeader
                  title="Activity"
                  subtitle={`${incident.worklog.length} entries · worklog`}
                />
                <WorklogThread entries={incident.worklog} />
                <WorklogComposer
                  incident={incident}
                  canWrite={canWrite}
                  authorName={user?.full_name ?? null}
                  onPosted={setOverride}
                />
              </Card>
            </div>

            {/* Right — properties + affected asset */}
            <div className="flex flex-col gap-5">
              <Card className="flex flex-col p-6">
                <SectionHeader title="Properties" />
                <dl className="mt-2 divide-y divide-line">
                  <PropertyRow label="Priority">
                    <PriorityControl
                      incident={incident}
                      canWriteOrg={canWriteOrg}
                      onChanged={setOverride}
                    />
                  </PropertyRow>
                  <PropertyRow label="Status">
                    <IncidentStatusPill status={incident.status} />
                  </PropertyRow>
                  <PropertyRow label="SLA">
                    <span className="inline-flex flex-col items-end gap-1">
                      <SlaPill state={incident.sla_state} />
                      {incident.sla_minutes_remaining !== null ? (
                        <span className="tnum text-xs font-medium text-muted">
                          {formatSlaClock(incident.sla_minutes_remaining)}
                        </span>
                      ) : null}
                    </span>
                  </PropertyRow>
                  <PropertyRow label="Category">
                    {incidentCategoryLabel(incident.category)}
                  </PropertyRow>
                  <PropertyRow label="Assignee">
                    <AssigneeControl
                      incident={incident}
                      canWriteOrg={canWriteOrg}
                      onChanged={setOverride}
                    />
                  </PropertyRow>
                  <PropertyRow label="Requester">{incident.requester}</PropertyRow>
                  <PropertyRow label="Opened">
                    <span className="tnum font-medium text-muted">
                      {formatDateTime(incident.created_at)}
                    </span>
                  </PropertyRow>
                  <PropertyRow label="Updated">
                    <span className="tnum font-medium text-muted">
                      {formatDateTime(incident.updated_at)}
                    </span>
                  </PropertyRow>
                </dl>
              </Card>

              <Card className="flex flex-col gap-3 p-6">
                <SectionHeader title="Affected asset" />
                {incident.affected_asset_tag ? (
                  <Link
                    to={`/assets/${incident.affected_asset_tag}`}
                    className="group flex items-center gap-3 rounded-tile bg-ground px-4 py-3 transition-colors hover:bg-line"
                  >
                    <span className="grid size-10 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                      <BoxIcon className="size-5" />
                    </span>
                    <span className="flex min-w-0 flex-col leading-tight">
                      <span className="tnum text-sm font-bold text-ink">
                        {incident.affected_asset_tag}
                      </span>
                      <span className="text-xs text-muted">View in the CMDB</span>
                    </span>
                  </Link>
                ) : (
                  <p className="flex items-center gap-2 text-sm text-muted">
                    <AlertTriangleIcon className="size-4 text-faint" />
                    No specific configuration item is linked to this incident.
                  </p>
                )}
              </Card>
            </div>
          </div>
        </>
      ) : null}

      {editing && incident ? (
        <EditIncidentDialog
          incident={incident}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            setOverride(updated);
            toast.success("Incident updated.");
          }}
        />
      ) : null}

      {resolving && incident ? (
        <ResolveIncidentDialog
          incident={incident}
          onClose={() => setResolving(false)}
          onResolved={(updated) => {
            setResolving(false);
            setOverride(updated);
            toast.success(`${updated.id} resolved.`);
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          remove.reset();
        }}
        onConfirm={() => void remove.mutate()}
        title="Withdraw this incident?"
        body={`${incident?.id} — ${incident?.summary} is removed from the queue. This can't be undone.`}
        confirmLabel="Withdraw"
        pendingLabel="Withdrawing…"
        pending={remove.pending}
        error={remove.error ? problemMessage(remove.error) : undefined}
      />
    </div>
  );
}
