import { useRef, useState } from "react";

import { createMilestone, updateMilestone } from "@/api/client";
import type { MemberOut, MilestoneOut, MilestoneStatus, ProjectOut } from "@/api/types";
import { MILESTONE_STATUS_LABEL } from "@/data/labels";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { maybe } from "@/lib/optional";
import { FormDialog, Select, TextField } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

const STATUS_OPTIONS: SelectOption[] = (
  ["on_track", "due_soon", "at_risk", "slipping", "completed"] as MilestoneStatus[]
).map((s) => ({ value: s, label: MILESTONE_STATUS_LABEL[s] }));

interface FormErrors {
  title?: string;
  project_code?: string;
  due_date?: string;
  progress_pct?: string;
}

/** Create or edit a milestone. The owning project is set once, at create, and never editable
 *  after — `MilestoneUpdate` has no `project_code`. */
export function MilestoneFormDialog({
  milestone,
  projects,
  members,
  defaultProjectCode,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  milestone: MilestoneOut | null;
  projects: ProjectOut[];
  members: MemberOut[];
  defaultProjectCode?: string;
  onClose: () => void;
  onSaved: (milestone: MilestoneOut, message: string) => void;
}) {
  const editing = milestone !== null;
  const [projectCode, setProjectCode] = useState(
    milestone?.project_code ?? defaultProjectCode ?? "",
  );
  const [title, setTitle] = useState(milestone?.title ?? "");
  const [ownerRef, setOwnerRef] = useState(milestone?.owner.id ?? "");
  const [dueDate, setDueDate] = useState(milestone?.due_date ?? "");
  const [status, setStatus] = useState<MilestoneStatus>(milestone?.status ?? "on_track");
  const [progress, setProgress] = useState(String(milestone?.progress_pct ?? 0));
  const [errors, setErrors] = useState<FormErrors>({});
  const projectRef = useRef<HTMLSelectElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () => {
      const owner_ref = ownerRef.trim() === "" ? null : ownerRef.trim();
      if (editing) {
        return updateMilestone(milestone.id, {
          title: title.trim(),
          owner_ref,
          due_date: dueDate.trim() === "" ? null : dueDate,
          status,
          progress_pct: Number(progress),
        });
      }
      return createMilestone({
        project_code: projectCode,
        title: title.trim(),
        owner_ref,
        due_date: dueDate,
        status,
        progress_pct: Number(progress),
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
    if (title.trim().length === 0) found.title = "Give the milestone a title.";
    if (!editing && dueDate.trim() === "") found.due_date = "Give the milestone a due date.";
    const progressNumber = Number(progress);
    if (!Number.isFinite(progressNumber) || progressNumber < 0 || progressNumber > 100) {
      found.progress_pct = "Enter a percentage between 0 and 100.";
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
  const ownerOptions: SelectOption[] = [
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
      title={editing ? `Edit ${milestone.title}` : "New milestone"}
      submitLabel={editing ? "Save changes" : "Create milestone"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing || defaultProjectCode ? titleRef : projectRef}
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
          {...maybe("error", errors.project_code ?? save.fieldErrors.project_code)}
        />
      ) : (
        <TextField
          label="Project"
          value={`${milestone.project_code} · ${milestone.project_name}`}
          disabled
          helper="A milestone doesn't change project after it's created."
        />
      )}

      <TextField
        ref={titleRef}
        label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        {...maybe("error", errors.title ?? save.fieldErrors.title)}
        />

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Owner"
          value={ownerRef}
          onChange={(e) => setOwnerRef(e.target.value)}
          options={ownerOptions}
          {...maybe("error", save.fieldErrors.owner_ref)}
        />
        <TextField
          label="Due date"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          optional={editing}
          {...maybe("helper", editing ? "Clear it to mark this milestone undated." : undefined)}
          {...maybe("error", errors.due_date ?? save.fieldErrors.due_date)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as MilestoneStatus)}
          options={STATUS_OPTIONS}
          {...maybe("error", save.fieldErrors.status)}
        />
        <TextField
          label="Progress (%)"
          inputMode="numeric"
          value={progress}
          onChange={(e) => setProgress(e.target.value)}
          {...maybe("error", errors.progress_pct ?? save.fieldErrors.progress_pct)}
        />
      </div>
    </FormDialog>
  );
}
