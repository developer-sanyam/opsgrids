import { useRef, useState } from "react";

import { createProject, updateProject } from "@/api/client";
import type { Person, Project, ProjectStatus } from "@/api/types";
import { minorToInput, parseMinor } from "@/lib/money";
import { withOptional } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, Switch, TextField, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

const CODE_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

const STATUS_OPTIONS: SelectOption[] = [
  { value: "on_track", label: "On track" },
  { value: "at_risk", label: "At risk" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Delivered" },
];

interface FormErrors {
  code?: string;
  name?: string;
  progress_pct?: string;
  budget_minor?: string;
  spent_minor?: string;
  currency?: string;
  dates?: string;
}

export function ProjectFormDialog({
  project,
  people,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  project: Project | null;
  people: Person[];
  onClose: () => void;
  onSaved: (project: Project, message: string) => void;
}) {
  const editing = project !== null;
  const [code, setCode] = useState(project?.code ?? "");
  const [name, setName] = useState(project?.name ?? "");
  const [client, setClient] = useState(project?.client ?? "");
  const [status, setStatus] = useState<ProjectStatus>(project?.status ?? "on_track");
  const [progress, setProgress] = useState(String(project?.progress_pct ?? 0));
  const [budget, setBudget] = useState(minorToInput(project?.budget_minor ?? 0));
  const [spent, setSpent] = useState(minorToInput(project?.spent_minor ?? 0));
  const [currency, setCurrency] = useState(project?.currency ?? "USD");
  const [ownerRef, setOwnerRef] = useState(project?.owner_id ?? "");
  const [startDate, setStartDate] = useState(project?.start_date ?? "");
  const [dueDate, setDueDate] = useState(project?.due_date ?? "");
  const [billable, setBillable] = useState(project?.billable ?? true);
  const [openTasks, setOpenTasks] = useState(String(project?.open_tasks ?? 0));

  const fields = {
    code,
    name,
    client,
    status,
    progress,
    budget,
    spent,
    currency,
    ownerRef,
    startDate,
    dueDate,
    billable,
    openTasks,
  };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [errors, setErrors] = useState<FormErrors>({});
  const codeRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () => {
      const budgetParsed = parseMinor(budget);
      const spentParsed = parseMinor(spent);
      const budget_minor = budgetParsed.ok ? budgetParsed.minor : 0;
      const spent_minor = spentParsed.ok ? spentParsed.minor : 0;
      const shared = {
        name: name.trim(),
        client: client.trim(),
        status,
        progress_pct: Number(progress),
        budget_minor,
        spent_minor,
        currency: currency.trim().toUpperCase(),
        owner_ref: ownerRef.trim() === "" ? null : ownerRef.trim(),
        start_date: startDate.trim() === "" ? null : startDate,
        due_date: dueDate.trim() === "" ? null : dueDate,
        billable,
        open_tasks: Number(openTasks),
      };
      return editing ? updateProject(project.code, shared) : createProject({ code: code.trim(), ...shared });
    },
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, editing ? `${saved.name} saved.` : `${saved.name} created.`);
      },
    },
  );

  function submit() {
    const found: FormErrors = {};
    if (!editing && !CODE_PATTERN.test(code.trim())) {
      found.code = "Use up to 40 letters, numbers, hyphens or underscores.";
    }
    if (name.trim().length === 0) found.name = "Give the engagement a name.";
    const progressNumber = Number(progress);
    if (!Number.isFinite(progressNumber) || progressNumber < 0 || progressNumber > 100) {
      found.progress_pct = "Enter a percentage between 0 and 100.";
    }
    const budgetParsed = parseMinor(budget);
    if (!budgetParsed.ok) found.budget_minor = budgetParsed.message;
    const spentParsed = parseMinor(spent);
    if (!spentParsed.ok) found.spent_minor = spentParsed.message;
    if (currency.trim().length !== 3) found.currency = "Use a 3-letter ISO currency code, like USD.";
    if (startDate.trim() !== "" && dueDate.trim() !== "" && dueDate < startDate) {
      found.dates = "The due date can't be before the start date.";
    }
    setErrors(found);
    if (Object.keys(found).length > 0) {
      if (found.code) codeRef.current?.focus();
      else if (found.name) nameRef.current?.focus();
      return;
    }
    void save.mutate(undefined);
  }

  const ownerOptions: SelectOption[] = [
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
      title={editing ? `Edit ${project.name}` : "New project"}
      submitLabel={editing ? "Save changes" : "Create project"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing ? nameRef : codeRef}
      size="lg"
      dirty={dirty}
      {...withOptional("error", nonFieldError)}
      {...withOptional("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {!editing ? (
          <TextField
            ref={codeRef}
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="PB-20"
            helper="Immutable once created — timesheets and tasks join on it."
            required
            {...withOptional("error", errors.code ?? save.fieldErrors.code)}
          />
        ) : (
          <TextField label="Code" value={project.code} disabled helper="Codes can't be changed." />
        )}
        <TextField
          ref={nameRef}
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          {...withOptional("error", errors.name ?? save.fieldErrors.name)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Client"
          value={client}
          onChange={(e) => setClient(e.target.value)}
          optional
          {...withOptional("error", save.fieldErrors.client)}
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as ProjectStatus)}
          options={STATUS_OPTIONS}
          {...withOptional("error", save.fieldErrors.status)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <TextField
          label="Progress (%)"
          inputMode="numeric"
          value={progress}
          onChange={(e) => setProgress(e.target.value)}
          {...withOptional("error", errors.progress_pct ?? save.fieldErrors.progress_pct)}
        />
        <TextField
          label={`Budget (${currency || "USD"})`}
          inputMode="decimal"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          helper="Major units — converted to minor units for the server."
          {...withOptional("error", errors.budget_minor ?? save.fieldErrors.budget_minor)}
        />
        <TextField
          label={`Spent (${currency || "USD"})`}
          inputMode="decimal"
          value={spent}
          onChange={(e) => setSpent(e.target.value)}
          {...withOptional("error", errors.spent_minor ?? save.fieldErrors.spent_minor)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          {...withOptional("error", errors.currency ?? save.fieldErrors.currency)}
        />
        <Select
          label="Owner"
          value={ownerRef}
          onChange={(e) => setOwnerRef(e.target.value)}
          options={ownerOptions}
          {...withOptional("error", save.fieldErrors.owner_ref)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Start date"
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
          optional
          {...withOptional("error", errors.dates ?? save.fieldErrors.start_date)}
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
        label="Open tasks"
        inputMode="numeric"
        value={openTasks}
        onChange={(e) => setOpenTasks(e.target.value)}
        helper="The stored open-work headline shown on the portfolio."
        {...withOptional("error", save.fieldErrors.open_tasks)}
      />

      <Switch
        label="Billable"
        checked={billable}
        onChange={setBillable}
        description={billable ? "Counted in the billable portfolio." : "Internal work — excluded from billable totals."}
      />
    </FormDialog>
  );
}
