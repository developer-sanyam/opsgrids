import { useRef, useState } from "react";

import { createTask, updateTask } from "@/api/client";
import type { MemberOut, ProjectOut, TaskOut, TaskPriority, TaskStage } from "@/api/types";
import { PRIORITY_LABEL, STAGE_LABEL } from "@/data/labels";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { maybe } from "@/lib/optional";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

const STAGE_OPTIONS: SelectOption[] = (
  ["backlog", "in_progress", "review", "done"] as TaskStage[]
).map((s) => ({ value: s, label: STAGE_LABEL[s] }));

const PRIORITY_OPTIONS: SelectOption[] = (
  ["low", "medium", "high", "urgent"] as TaskPriority[]
).map((p) => ({ value: p, label: PRIORITY_LABEL[p] }));

interface FormErrors {
  title?: string;
  project_code?: string;
  points?: string;
}

/** Create or edit a board card. The owning project is set once, at create, and never editable
 *  after — `TaskUpdate` has no `project_code`. */
export function TaskFormDialog({
  task,
  projects,
  members,
  defaultProjectCode,
  defaultStage,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  task: TaskOut | null;
  projects: ProjectOut[];
  members: MemberOut[];
  defaultProjectCode?: string;
  defaultStage?: TaskStage;
  onClose: () => void;
  onSaved: (task: TaskOut, message: string) => void;
}) {
  const editing = task !== null;
  const [projectCode, setProjectCode] = useState(task?.project_code ?? defaultProjectCode ?? "");
  const [title, setTitle] = useState(task?.title ?? "");
  const [assigneeRef, setAssigneeRef] = useState(task?.assignee.id ?? "");
  const [stage, setStage] = useState<TaskStage>(task?.stage ?? defaultStage ?? "backlog");
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? "medium");
  const [dueDate, setDueDate] = useState(task?.due_date ?? "");
  const [points, setPoints] = useState(String(task?.points ?? 0));

  const fields = { projectCode, title, assigneeRef, stage, priority, dueDate, points };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [errors, setErrors] = useState<FormErrors>({});
  const projectRef = useRef<HTMLSelectElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () => {
      const assignee_ref = assigneeRef.trim() === "" ? null : assigneeRef.trim();
      const due_date = dueDate.trim() === "" ? null : dueDate;
      if (editing) {
        return updateTask(task.id, {
          title: title.trim(),
          assignee_ref,
          stage,
          priority,
          due_date,
          points: Number(points),
        });
      }
      return createTask({
        project_code: projectCode,
        title: title.trim(),
        assignee_ref,
        stage,
        priority,
        due_date,
        points: Number(points),
      });
    },
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, editing ? `${saved.title} saved.` : `${saved.title} created.`);
      },
    },
  );

  function submit() {
    const found: FormErrors = {};
    if (!editing && projectCode.trim() === "") found.project_code = "Choose the project.";
    if (title.trim().length === 0) found.title = "Give the card a title.";
    const pointsNumber = Number(points);
    if (!Number.isFinite(pointsNumber) || pointsNumber < 0) {
      found.points = "Enter a whole number, 0 or more.";
    }
    setErrors(found);
    if (Object.keys(found).length > 0) {
      if (found.project_code) projectRef.current?.focus();
      else if (found.title) titleRef.current?.focus();
      return;
    }
    void save.mutate(undefined);
  }

  const projectOptions: SelectOption[] = projects.map((p) => ({
    value: p.code,
    label: `${p.code} · ${p.name}`,
  }));
  const assigneeOptions: SelectOption[] = [
    { value: "", label: "Unassigned" },
    ...members.map((m) => ({ value: m.id, label: m.name })),
  ];

  const nonFieldError =
    save.error && Object.keys(save.fieldErrors).length === 0 ? problemMessage(save.error) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${task.title}` : "New card"}
      submitLabel={editing ? "Save changes" : "Create card"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing || defaultProjectCode ? titleRef : projectRef}
      dirty={dirty}
      {...maybe("error", nonFieldError)}
      {...maybe("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      {!editing ? (
        <Select
          ref={projectRef}
          label="Project"
          value={projectCode}
          onChange={(e) => setProjectCode(e.target.value)}
          options={projectOptions}
          placeholder="Choose a project…"
          disabled={defaultProjectCode !== undefined}
          required
          {...maybe("error", errors.project_code ?? save.fieldErrors.project_code)}
        />
      ) : (
        <TextField
          label="Project"
          value={task.project_code}
          disabled
          helper="A card doesn't change project after it's created."
        />
      )}

      <TextField
        ref={titleRef}
        label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        required
        {...maybe("error", errors.title ?? save.fieldErrors.title)}
        />

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Assignee"
          value={assigneeRef}
          onChange={(e) => setAssigneeRef(e.target.value)}
          options={assigneeOptions}
          {...maybe("error", save.fieldErrors.assignee_ref)}
        />
        <Select
          label="Stage"
          value={stage}
          onChange={(e) => setStage(e.target.value as TaskStage)}
          options={STAGE_OPTIONS}
          {...maybe("error", save.fieldErrors.stage)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Select
          label="Priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
          options={PRIORITY_OPTIONS}
          {...maybe("error", save.fieldErrors.priority)}
        />
        <TextField
          label="Due date"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.due_date)}
        />
        <TextField
          label="Points"
          inputMode="numeric"
          value={points}
          onChange={(e) => setPoints(e.target.value)}
          {...maybe("error", errors.points ?? save.fieldErrors.points)}
        />
      </div>
    </FormDialog>
  );
}
