import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { assignTicket, changeTicketStatus, getTicket, listAgents, replyToTicket } from "@/api/client";
import type { Agent, Message, Ticket, TicketStatus } from "@/api/types";
import { Can, problemMessage, useAsync, useAuth, useCan, useMutation } from "@opsgrids/client";
import { agentById } from "@/data/derive";
import { errorProp } from "@/lib/optionalProp";
import { ALLOWED_TRANSITIONS } from "@/lib/ticketLifecycle";
import {
  ChevronRightIcon,
  CodeIcon,
  GlobeIcon,
  InlineAlert,
  MailIcon,
  MessageSquareIcon,
  PhoneIcon,
  ReplyIcon,
  Select,
  Switch,
  TextArea,
  cn,
  useToast,
} from "@opsgrids/ui";
import { channelLabel, formatDateTime, initials, statusLabel } from "@/lib/format";
import {
  Avatar,
  Button,
  Card,
  ErrorState,
  PriorityPill,
  SectionHeader,
  Skeleton,
  SlaPill,
  Tag,
  TicketStatusPill,
} from "@/ui";
import { EditTicketDialog } from "@/ui/EditTicketDialog";

const CAN_WRITE = "support.desk.ticket.write";

const CHANNEL_ICON: Record<Ticket["channel"], typeof MailIcon> = {
  email: MailIcon,
  chat: MessageSquareIcon,
  web: GlobeIcon,
  phone: PhoneIcon,
  api: CodeIcon,
};

/** One message in the thread — styled by who wrote it, with internal notes clearly set apart. */
function MessageBubble({ message }: { message: Message }) {
  if (message.author_type === "system") {
    return (
      <li className="flex items-center gap-3 py-1 text-xs text-faint">
        <span className="h-px flex-1 bg-line" aria-hidden />
        <span>{message.body}</span>
        <span className="h-px flex-1 bg-line" aria-hidden />
      </li>
    );
  }

  const isAgent = message.author_type === "agent";
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        {isAgent ? (
          <span className="grid size-8 place-items-center rounded-full bg-accent text-xs font-bold text-white">
            {initials(message.author_name)}
          </span>
        ) : (
          <Avatar name={message.author_name} size="sm" />
        )}
        <span className="text-sm font-semibold text-ink">{message.author_name}</span>
        <span className="text-xs font-medium text-faint">
          {isAgent ? "Agent" : "Customer"}
        </span>
        {message.internal ? (
          <span className="rounded-full bg-late-soft px-2 py-0.5 text-[0.7rem] font-bold text-late">
            Internal note
          </span>
        ) : null}
        <span className="ml-auto text-xs text-faint tnum">{formatDateTime(message.at)}</span>
      </div>
      <div
        className={cn(
          "rounded-card px-4 py-3 text-sm leading-relaxed ml-10",
          message.internal
            ? "border border-late/30 bg-late-soft/50 text-ink-soft"
            : isAgent
              ? "bg-accent-soft text-ink"
              : "bg-ground text-ink-soft",
        )}
      >
        {message.body}
      </div>
    </li>
  );
}

/** A labelled property row in the sidebar. */
function PropertyRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <div className="text-right">{children}</div>
    </div>
  );
}

function AssigneeControl({
  ticket,
  agents,
  canWrite,
  onChanged,
}: {
  ticket: Ticket;
  agents: Agent[];
  canWrite: boolean;
  onChanged: (ticket: Ticket) => void;
}) {
  const toast = useToast();
  const assign = useMutation((assigneeId: string | null) => assignTicket(ticket.id, assigneeId), {
    onSuccess: (updated, assigneeId) => {
      onChanged(updated);
      const agent = assigneeId ? agentById(agents, assigneeId) : null;
      toast.success(agent ? `Assigned to ${agent.name}.` : "Unassigned.");
    },
  });

  if (!canWrite) {
    const assignee = agentById(agents, ticket.assignee_id);
    return assignee ? (
      <span className="inline-flex items-center gap-2">
        <Avatar name={assignee.name} size="sm" />
        <span className="text-sm font-semibold text-ink">{assignee.name}</span>
      </span>
    ) : (
      <span className="text-sm font-medium text-faint">Unassigned</span>
    );
  }

  return (
    <div className="flex w-40 flex-col items-end gap-1.5">
      <Select
        label="Assignee"
        containerClassName="w-full"
        value={ticket.assignee_id ?? ""}
        onChange={(e) => void assign.mutate(e.target.value === "" ? null : e.target.value)}
        disabled={assign.pending}
        // "Unassigned" is a real, selectable option here (not the placeholder) — this control
        // both assigns and unassigns an already-assigned ticket, unlike the create form's
        // "leave it unset" default.
        options={[
          { value: "", label: "Unassigned" },
          ...agents.map((a) => ({ value: a.id, label: a.name })),
        ]}
      />
      {assign.error ? (
        <InlineAlert tone="danger" title={problemMessage(assign.error)} className="w-full" />
      ) : null}
    </div>
  );
}

function StatusControl({
  ticket,
  canWrite,
  onChanged,
}: {
  ticket: Ticket;
  canWrite: boolean;
  onChanged: (ticket: Ticket) => void;
}) {
  const toast = useToast();
  const options = ALLOWED_TRANSITIONS[ticket.status];
  const change = useMutation((target: TicketStatus) => changeTicketStatus(ticket.id, target), {
    onSuccess: (updated) => {
      onChanged(updated);
      toast.success(`Status changed to ${statusLabel(updated.status)}.`);
    },
  });

  if (!canWrite || options.length === 0) {
    return <TicketStatusPill status={ticket.status} />;
  }

  return (
    <div className="flex w-40 flex-col items-end gap-1.5">
      <TicketStatusPill status={ticket.status} />
      <Select
        label="Move ticket to"
        containerClassName="w-full"
        value=""
        onChange={(e) => {
          const value = e.target.value as TicketStatus | "";
          if (value) void change.mutate(value);
        }}
        placeholder="Change status…"
        disabled={change.pending}
        options={options.map((s) => ({ value: s, label: statusLabel(s) }))}
      />
      {change.error ? (
        <InlineAlert tone="danger" title={problemMessage(change.error)} className="w-full" />
      ) : null}
    </div>
  );
}

function TicketProperties({
  ticket,
  agents,
  canWrite,
  onChanged,
}: {
  ticket: Ticket;
  agents: Agent[];
  canWrite: boolean;
  onChanged: (ticket: Ticket) => void;
}) {
  const ChannelIcon = CHANNEL_ICON[ticket.channel];
  return (
    <Card className="flex flex-col gap-1 p-6">
      <SectionHeader title="Properties" />

      <div className="mt-1 flex items-center gap-3 rounded-tile bg-ground p-3">
        <Avatar name={ticket.requester.name} size="md" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-sm font-bold text-ink">{ticket.requester.name}</span>
          <span className="truncate text-xs text-muted">{ticket.requester.email}</span>
        </div>
        <span
          className={cn(
            "ml-auto rounded-full px-2 py-0.5 text-[0.7rem] font-bold",
            ticket.requester.kind === "staff"
              ? "bg-accent-soft text-accent-ink"
              : "bg-info-soft text-info",
          )}
        >
          {ticket.requester.kind === "staff" ? "Staff" : "Customer"}
        </span>
      </div>

      <div className="mt-2 divide-y divide-line/70">
        <PropertyRow label="Company">
          <span className="text-sm font-semibold text-ink">{ticket.requester.company}</span>
        </PropertyRow>
        <PropertyRow label="Channel">
          <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft">
            <ChannelIcon className="size-4 text-faint" />
            {channelLabel(ticket.channel)}
          </span>
        </PropertyRow>
        <PropertyRow label="Priority">
          <PriorityPill priority={ticket.priority} />
        </PropertyRow>
        <PropertyRow label="Status">
          <StatusControl ticket={ticket} canWrite={canWrite} onChanged={onChanged} />
        </PropertyRow>
        <PropertyRow label="Assignee">
          <AssigneeControl
            ticket={ticket}
            agents={agents}
            canWrite={canWrite}
            onChanged={onChanged}
          />
        </PropertyRow>
        <PropertyRow label="SLA">
          <SlaPill ticket={ticket} />
        </PropertyRow>
        <PropertyRow label="Opened">
          <span className="text-sm text-ink-soft tnum">{formatDateTime(ticket.created_at)}</span>
        </PropertyRow>
        <PropertyRow label="First response">
          <span className="text-sm text-ink-soft tnum">
            {formatDateTime(ticket.first_response_at)}
          </span>
        </PropertyRow>
      </div>

      <div className="mt-2 flex flex-col gap-2 border-t border-line pt-4">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">Tags</span>
        <div className="flex flex-wrap gap-1.5">
          {ticket.tags.length === 0 ? (
            <span className="text-sm text-faint">No tags</span>
          ) : (
            ticket.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)
          )}
        </div>
      </div>
    </Card>
  );
}

function ReplyBox({
  ticketId,
  authorName,
  canWrite,
  onSent,
}: {
  ticketId: string;
  authorName: string | null;
  canWrite: boolean;
  onSent: (ticket: Ticket) => void;
}) {
  const toast = useToast();
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const [bodyError, setBodyError] = useState<string | undefined>(undefined);

  const send = useMutation(
    (input: { body: string; internal: boolean }) =>
      replyToTicket(ticketId, {
        body: input.body,
        author_name: authorName ?? "",
        internal: input.internal,
      }),
    {
      onSuccess: (updated, input) => {
        setBody("");
        setInternal(false);
        setBodyError(undefined);
        onSent(updated);
        toast.success(input.internal ? "Internal note added." : "Reply sent.");
      },
    },
  );

  if (!canWrite) return null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setBodyError(undefined);
    const trimmed = body.trim();
    if (trimmed.length < 1) {
      setBodyError("Write something before sending.");
      return;
    }
    void send.mutate({ body: trimmed, internal });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      {authorName === null ? (
        <InlineAlert tone="warning" title="Your account has no display name set.">
          Ask an admin to add one before you can reply — the thread needs a name to attribute the
          message to.
        </InlineAlert>
      ) : null}
      <TextArea
        label="Reply"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        placeholder={internal ? "Add an internal note — visible to agents only…" : `Reply as ${authorName ?? "…"}…`}
        {...errorProp(bodyError)}
        disabled={authorName === null || send.pending}
      />
      <div className="flex flex-wrap items-start gap-3">
        <Switch
          checked={internal}
          onChange={setInternal}
          label="Internal note"
          description="Visible to agents only, never the requester."
          disabled={send.pending}
        />
        <Button
          type="submit"
          size="sm"
          loading={send.pending}
          disabled={authorName === null || body.trim().length === 0}
          leadingIcon={<ReplyIcon className="size-4" />}
          className="ml-auto"
        >
          {send.pending ? "Sending…" : internal ? "Add note" : "Send reply"}
        </Button>
      </div>
      {send.error ? <InlineAlert tone="danger" title={problemMessage(send.error)} /> : null}
    </form>
  );
}

export function TicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const toast = useToast();
  const canWrite = useCan(CAN_WRITE, "ORG");
  const loader = useCallback(() => getTicket(id ?? ""), [id]);
  const ticketState = useAsync(loader, [id]);
  const agentsState = useAsync(listAgents, []);

  // Every write here (`assign`, `status`, `reply`) returns the full updated ticket, so a
  // successful mutation replaces the screen's copy directly rather than triggering a refetch
  // (`ticketState.reload()` would drop straight back to the loading skeleton). `override` resets
  // whenever the id changes, so navigating from one ticket to the next never shows a stale one.
  const [override, setOverride] = useState<Ticket | null>(null);
  useEffect(() => {
    setOverride(null);
  }, [id]);
  const ticket = override ?? (ticketState.status === "success" ? ticketState.data : null);
  const setTicket = setOverride;

  const [editing, setEditing] = useState(false);

  const agents = agentsState.status === "success" ? agentsState.data : [];

  const statusMutation = useMutation(
    (target: TicketStatus) => changeTicketStatus(ticket?.id ?? "", target),
    {
      onSuccess: (updated) => {
        setTicket(updated);
        toast.success(`Status changed to ${statusLabel(updated.status)}.`);
      },
    },
  );

  const canSolve = ticket ? ALLOWED_TRANSITIONS[ticket.status].includes("solved") : false;
  const canReopen = ticket ? ticket.status === "solved" : false;

  const thread = useMemo(() => ticket?.messages ?? [], [ticket]);

  return (
    <div className="rise flex flex-col gap-5">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm font-medium">
        <Link to="/queue" className="text-muted hover:text-ink transition-colors">
          Queue
        </Link>
        <ChevronRightIcon className="size-4 text-faint" />
        <span className="text-ink-soft tnum">{id}</span>
      </nav>

      {ticketState.status === "loading" ? (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <div className="flex flex-col gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-tile" />
              ))}
            </div>
          </Card>
          <Card className="p-6">
            <Skeleton className="h-72 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {ticketState.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            message={ticketState.error.message}
            action={<Button onClick={ticketState.reload}>Try again</Button>}
          />
          <div className="mt-4 flex justify-center">
            <Link
              to="/queue"
              className="rounded-full bg-ink px-5 h-11 inline-flex items-center font-semibold text-white hover:bg-ink-soft transition-colors"
            >
              Back to queue
            </Link>
          </div>
        </Card>
      ) : null}

      {ticket ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-2">
              <h1 className="display text-2xl font-extrabold leading-tight sm:text-[1.9rem]">
                {ticket.subject}
              </h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-muted tnum">{ticket.id}</span>
                <span className="text-faint">·</span>
                <PriorityPill priority={ticket.priority} />
                <TicketStatusPill status={ticket.status} />
                <SlaPill ticket={ticket} />
              </div>
            </div>
            <Can permission={CAN_WRITE} scope="ORG">
              <div className="flex items-center gap-2">
                <Button variant="outline" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                {canReopen ? (
                  <Button
                    variant="outline"
                    loading={statusMutation.pending}
                    onClick={() => void statusMutation.mutate("open")}
                  >
                    Reopen
                  </Button>
                ) : null}
                {canSolve ? (
                  <Button
                    loading={statusMutation.pending}
                    onClick={() => void statusMutation.mutate("solved")}
                  >
                    Mark solved
                  </Button>
                ) : null}
              </div>
            </Can>
          </header>
          {statusMutation.error ? (
            <InlineAlert tone="danger" title={problemMessage(statusMutation.error)} />
          ) : null}

          <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-3">
            {/* Conversation */}
            <Card className="flex flex-col gap-5 p-6 lg:col-span-2">
              <SectionHeader
                title="Conversation"
                subtitle={`${thread.length} message${thread.length === 1 ? "" : "s"}`}
              />
              <ul className="flex flex-col gap-5" aria-live="polite">
                {thread.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
              </ul>
              <div className="border-t border-line pt-5">
                <ReplyBox
                  ticketId={ticket.id}
                  authorName={user?.full_name ?? null}
                  canWrite={canWrite}
                  onSent={setTicket}
                />
              </div>
            </Card>

            {/* Properties */}
            <TicketProperties
              ticket={ticket}
              agents={agents}
              canWrite={canWrite}
              onChanged={setTicket}
            />
          </div>
        </>
      ) : null}

      {editing && ticket ? (
        <EditTicketDialog
          ticket={ticket}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setTicket(updated);
            toast.success("Ticket updated.");
          }}
        />
      ) : null}
    </div>
  );
}
