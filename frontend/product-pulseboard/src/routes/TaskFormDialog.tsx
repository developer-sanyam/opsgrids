import { useRef, useState } from "react";

import { createTask } from "@/api/client";
import type { Person, Priority, Project, Task, TaskStatus } from "@/api/types";
import { withOptional } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

const KEY_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

const STATUS_OPTIONS: SelectOption[] = [
  { value: "backlog", label: "Backlog" },
  { value: "in_progress", label: "In progress" },
  { value: "review", label: "Review" },
  { value: "done", label: "Done" },
];

const PRIORITY_OPTIONS: SelectOption[] = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

interface FormErrors {
  key?: string;
  project_code?: string;
  title?: string;
  estimate_hours?: string;
}

/** Create a board card. There is no edit-by-form for a card: its stage, assignee, estimate and
 * priority all move from the board itself (the shared board's row actions in `Board.tsx`), which is the same
 * `PATCH /projects/tasks/{key}` this dialog's create leads into. */
export function TaskFormDialog({
  projects,
  people,
  defaultProjectCode,
  onClose,
  onSaved,
}: {
  projects: Project[];
  people: Person[];
  defaultProjectCode?: string;
  onClose: () => void;
  onSaved: (task: Task, message: string) => void;
}) {
  const [key, setKey] = useState("");
  const [projectCode, setProjectCode] = useState(defaultProjectCode ?? projects[0]?.code ?? "");
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<TaskStatus>("backlog");
  const [assigneeRef, setAssigneeRef] = useState("");
  const [estimate, setEstimate] = useState("0");
  const [priority, setPriority] = useState<Priority>("medium");
  const [tags, setTags] = useState("");
  const [dueDate, setDueDate] = useState("");

  const fields = { key, projectCode, title, status, assigneeRef, estimate, priority, tags, dueDate };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [errors, setErrors] = useState<FormErrors>({});
  const keyRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      createTask({
        key: key.trim(),
        project_code: projectCode,
        title: title.trim(),
        status,
        assignee_ref: assigneeRef.trim() === "" ? null : assigneeRef.trim(),
        estimate_hours: Number(estimate),
        priority,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter((t) => t.length > 0),
        due_date: dueDate.trim() === "" ? null : dueDate,
      }),
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, `${saved.key} added to ${status === "backlog" ? "Backlog" : "the board"}.`);
      },
    },
  );

  function submit() {
    const found: FormErrors = {};
    if (!KEY_PATTERN.test(key.trim())) found.key = "Use up to 40 letters, numbers, hyphens or underscores.";
    if (projectCode.trim() === "") found.project_code = "Choose the engagement this card belongs to.";
    if (title.trim().length === 0) found.title = "Give the card a title.";
    const estimateNumber = Number(estimate);
    if (!Number.isFinite(estimateNumber) || estimateNumber < 0) {
      found.estimate_hours = "Enter a whole number of hours, 0 or more.";
    }
    setErrors(found);
    if (Object.keys(found).length > 0) {
      if (found.key) keyRef.current?.focus();
      else if (found.title) titleRef.current?.focus();
      return;
    }
    void save.mutate(undefined);
  }

  const projectOptions: SelectOption[] = projects.map((p) => ({ value: p.code, label: `${p.code} — ${p.name}` }));
  const assigneeOptions: SelectOption[] = [
    { value: "", label: "Unassigned" },
    ...people.map((p) => ({ value: p.id, label: p.name })),
  ];

  const nonFieldError =
    save.error && Object.keys(save.fieldErrors).length === 0 ? problemMessage(save.error) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New task"
      submitLabel="Create task"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={keyRef}
      size="lg"
      dirty={dirty}
      {...withOptional("error", nonFieldError)}
      {...withOptional("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={keyRef}
          label="Key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="PB-300"
          required
          {...withOptional("error", errors.key ?? save.fieldErrors.key)}
        />
        <Select
          label="Engagement"
          value={projectCode}
          onChange={(e) => setProjectCode(e.target.value)}
          options={projectOptions}
          required
          {...withOptional("error", errors.project_code ?? save.fieldErrors.project_code)}
        />
      </div>

      <TextField
        ref={titleRef}
        label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
        {...withOptional("error", errors.title ?? save.fieldErrors.title)}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Column"
          value={status}
          onChange={(e) => setStatus(e.target.value as TaskStatus)}
          options={STATUS_OPTIONS}
          {...withOptional("error", save.fieldErrors.status)}
        />
        <Select
          label="Assignee"
          value={assigneeRef}
          onChange={(e) => setAssigneeRef(e.target.value)}
          options={assigneeOptions}
          {...withOptional("error", save.fieldErrors.assignee_ref)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <TextField
          label="Estimate (hours)"
          inputMode="numeric"
          value={estimate}
          onChange={(e) => setEstimate(e.target.value)}
          {...withOptional("error", errors.estimate_hours ?? save.fieldErrors.estimate_hours)}
        />
        <Select
          label="Priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as Priority)}
          options={PRIORITY_OPTIONS}
          {...withOptional("error", save.fieldErrors.priority)}
        />
        <TextField
          label="Due date"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          optional
          {...withOptional("error", save.fieldErrors.due_date)}
        />
      </div>

      <TextField
        label="Tags"
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        placeholder="frontend, design"
        helper="Comma-separated, up to 60 characters each."
        optional
        {...withOptional("error", save.fieldErrors.tags)}
      />
    </FormDialog>
  );
}
