import { useRef, useState } from "react";

import { createTicket, listAgents } from "@/api/client";
import type { Agent, Channel, Priority, Ticket, TicketCreateIn } from "@/api/types";
import { problemMessage, useAsync, useMutation } from "@opsgrids/client";
import type { ApiError } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField } from "@opsgrids/ui";
import { errorProp } from "@/lib/optionalProp";

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "urgent", label: "Urgent" },
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

const CHANNEL_OPTIONS: { value: Channel; label: string }[] = [
  { value: "web", label: "Web form" },
  { value: "email", label: "Email" },
  { value: "chat", label: "Live chat" },
  { value: "phone", label: "Phone" },
  { value: "api", label: "API" },
];

/** A simple, permissive shape check — the server's `EmailStr` is the real validator; this only
 *  catches an obviously incomplete address before a round trip. */
function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

interface NewTicketDialogProps {
  onClose: () => void;
  onCreated: (ticket: Ticket) => void;
}

/**
 * "New ticket" — opened from the Overview and from the Queue's `PageHeader`. Mounted only while
 * the dialog is open, so closing it is the reset (CRUD.md).
 */
export function NewTicketDialog({ onClose, onCreated }: NewTicketDialogProps) {
  const agentsState = useAsync(listAgents, []);
  const agents: Agent[] = agentsState.status === "success" ? agentsState.data : [];

  const [subject, setSubject] = useState("");
  const [requesterName, setRequesterName] = useState("");
  const [requesterEmail, setRequesterEmail] = useState("");
  const [requesterCompany, setRequesterCompany] = useState("");
  const [priority, setPriority] = useState<Priority>("normal");
  const [channel, setChannel] = useState<Channel>("web");
  const [assigneeId, setAssigneeId] = useState<string>("");
  const [tags, setTags] = useState("");
  const [body, setBody] = useState("");

  const [subjectError, setSubjectError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [bodyError, setBodyError] = useState<string | undefined>(undefined);

  const subjectRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const save = useMutation((input: TicketCreateIn) => createTicket(input), {
    onSuccess: (created) => {
      onClose();
      onCreated(created);
    },
  });

  function submit() {
    setSubjectError(undefined);
    setNameError(undefined);
    setEmailError(undefined);
    setBodyError(undefined);

    const trimmedSubject = subject.trim();
    const trimmedName = requesterName.trim();
    const trimmedEmail = requesterEmail.trim();
    const trimmedBody = body.trim();

    if (trimmedSubject.length < 1 || trimmedSubject.length > 300) {
      setSubjectError("Give the ticket a subject, up to 300 characters.");
      subjectRef.current?.focus();
      return;
    }
    if (trimmedName.length < 1 || trimmedName.length > 160) {
      setNameError("Enter the requester's name, up to 160 characters.");
      nameRef.current?.focus();
      return;
    }
    if (!looksLikeEmail(trimmedEmail) || trimmedEmail.length > 254) {
      setEmailError("Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    if (trimmedBody.length < 1) {
      setBodyError("Describe what the requester needs help with.");
      bodyRef.current?.focus();
      return;
    }

    void save.mutate({
      subject: trimmedSubject,
      requester: {
        name: trimmedName,
        email: trimmedEmail,
        company: requesterCompany.trim(),
        kind: "customer",
      },
      priority,
      channel,
      ...(assigneeId ? { assignee_id: assigneeId } : {}),
      tags: tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      body: trimmedBody,
    });
  }

  const topLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0
      ? problemMessage(save.error)
      : undefined;
  const requestId =
    topLevelError !== undefined ? (save.error as ApiError).problem?.request_id : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New ticket"
      submitLabel="Create ticket"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={subjectRef}
      size="lg"
      {...errorProp(topLevelError)}
      {...(requestId !== undefined ? { reference: requestId } : {})}
    >
      <TextField
        ref={subjectRef}
        label="Subject"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        {...errorProp(subjectError ?? save.fieldErrors.subject)}
        maxLength={300}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={nameRef}
          label="Requester name"
          value={requesterName}
          onChange={(e) => setRequesterName(e.target.value)}
          {...errorProp(nameError ?? save.fieldErrors["requester.name"])}
        />
        <TextField
          ref={emailRef}
          label="Requester email"
          type="email"
          value={requesterEmail}
          onChange={(e) => setRequesterEmail(e.target.value)}
          {...errorProp(emailError ?? save.fieldErrors["requester.email"])}
        />
      </div>
      <TextField
        label="Company"
        optional
        value={requesterCompany}
        onChange={(e) => setRequesterCompany(e.target.value)}
        {...errorProp(save.fieldErrors["requester.company"])}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Select
          label="Priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as Priority)}
          options={PRIORITY_OPTIONS}
          {...errorProp(save.fieldErrors.priority)}
        />
        <Select
          label="Channel"
          value={channel}
          onChange={(e) => setChannel(e.target.value as Channel)}
          options={CHANNEL_OPTIONS}
          {...errorProp(save.fieldErrors.channel)}
        />
        <Select
          label="Assignee"
          value={assigneeId}
          onChange={(e) => setAssigneeId(e.target.value)}
          placeholder="Unassigned"
          options={agents.map((a) => ({ value: a.id, label: a.name }))}
          {...errorProp(save.fieldErrors.assignee_id)}
        />
      </div>
      <TextField
        label="Tags"
        optional
        helper="Comma-separated, e.g. billing, urgent."
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        {...errorProp(save.fieldErrors.tags)}
      />
      <TextArea
        ref={bodyRef}
        label="Message"
        helper="Becomes the ticket's opening message, from the requester."
        rows={4}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        {...errorProp(bodyError ?? save.fieldErrors.body)}
      />
    </FormDialog>
  );
}
