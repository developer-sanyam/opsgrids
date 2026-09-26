import { useRef, useState } from "react";

import { createProject, updateProject } from "@/api/client";
import type { MemberOut, ProjectHealth, ProjectOut, ProjectPhase } from "@/api/types";
import { HEALTH_LABEL, PHASE_LABEL } from "@/data/labels";
import { minorToInput, parseMinor } from "@/lib/money";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { maybe } from "@/lib/optional";
import { FormDialog, Select, TextArea, TextField, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

const CODE_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

const PHASE_OPTIONS: SelectOption[] = (
  ["discovery", "design", "build", "stabilize", "launch", "live"] as ProjectPhase[]
).map((p) => ({ value: p, label: PHASE_LABEL[p] }));

const HEALTH_OPTIONS: SelectOption[] = (
  ["on_track", "at_risk", "delayed"] as ProjectHealth[]
).map((h) => ({ value: h, label: HEALTH_LABEL[h] }));

interface FormErrors {
  code?: string;
  name?: string;
  progress_pct?: string;
  budget_minor?: string;
  spent_minor?: string;
  currency?: string;
  start_date?: string;
  target_date?: string;
  team_size?: string;
}

/** Create or edit a project. `code` is set once, at create, and never editable after — it is the
 *  API identity every milestone and task on it carries. */
export function ProjectFormDialog({
  project,
  members,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  project: ProjectOut | null;
  members: MemberOut[];
  onClose: () => void;
  onSaved: (project: ProjectOut, message: string) => void;
}) {
  const editing = project !== null;
  const [code, setCode] = useState(project?.code ?? "");
  const [name, setName] = useState(project?.name ?? "");
  const [client, setClient] = useState(project?.client ?? "");
  const [ownerRef, setOwnerRef] = useState(project?.owner.id ?? "");
  const [phase, setPhase] = useState<ProjectPhase>(project?.phase ?? "discovery");
  const [health, setHealth] = useState<ProjectHealth>(project?.health ?? "on_track");
  const [progress, setProgress] = useState(String(project?.progress_pct ?? 0));
  const [startDate, setStartDate] = useState(project?.start_date ?? "");
  const [targetDate, setTargetDate] = useState(project?.target_date ?? "");
  const [budget, setBudget] = useState(minorToInput(project?.budget_minor ?? 0, project?.currency ?? "INR"));
  const [spent, setSpent] = useState(minorToInput(project?.spent_minor ?? 0, project?.currency ?? "INR"));
  const [currency, setCurrency] = useState(project?.currency ?? "INR");
  const [teamSize, setTeamSize] = useState(String(project?.team_size ?? 0));
  const [summary, setSummary] = useState(project?.summary ?? "");

  const fields = {
    code,
    name,
    client,
    ownerRef,
    phase,
    health,
    progress,
    startDate,
    targetDate,
    budget,
    spent,
    currency,
    teamSize,
    summary,
  };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [errors, setErrors] = useState<FormErrors>({});
  const codeRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () => {
      const budgetParsed = parseMinor(budget, currency);
      const spentParsed = parseMinor(spent, currency);
      const shared = {
        name: name.trim(),
        client: client.trim(),
        owner_ref: ownerRef.trim() === "" ? null : ownerRef.trim(),
        phase,
        health,
        progress_pct: Number(progress),
        start_date: startDate,
        target_date: targetDate,
        budget_minor: budgetParsed.ok ? budgetParsed.minor : 0,
        spent_minor: spentParsed.ok ? spentParsed.minor : 0,
        currency: currency.trim().toUpperCase(),
        team_size: Number(teamSize),
        summary: summary.trim(),
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
    if (name.trim().length === 0) found.name = "Give the project a name.";
    const progressNumber = Number(progress);
    if (!Number.isFinite(progressNumber) || progressNumber < 0 || progressNumber > 100) {
      found.progress_pct = "Enter a percentage between 0 and 100.";
    }
    const budgetParsed = parseMinor(budget, currency);
    if (!budgetParsed.ok) found.budget_minor = budgetParsed.message;
    const spentParsed = parseMinor(spent, currency);
    if (!spentParsed.ok) found.spent_minor = spentParsed.message;
    if (currency.trim().length !== 3) found.currency = "Use a 3-letter ISO currency code, like INR.";
    if (startDate.trim() === "") found.start_date = "Give the project a start date.";
    if (targetDate.trim() === "") found.target_date = "Give the project a target date.";
    else if (startDate.trim() !== "" && targetDate < startDate) {
      found.target_date = "The target date can't be before the start date.";
    }
    const teamNumber = Number(teamSize);
    if (!Number.isFinite(teamNumber) || teamNumber < 0) {
      found.team_size = "Enter a whole number, 0 or more.";
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
    ...members.map((m) => ({ value: m.id, label: m.name })),
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
      {...maybe("error", nonFieldError)}
      {...maybe("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {!editing ? (
          <TextField
            ref={codeRef}
            label="Code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="PRJ-104"
            helper="Immutable once created — milestones and tasks join on it."
            required
            {...maybe("error", errors.code ?? save.fieldErrors.code)}
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
          {...maybe("error", errors.name ?? save.fieldErrors.name)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Client"
          value={client}
          onChange={(e) => setClient(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.client)}
        />
        <Select
          label="Owner"
          value={ownerRef}
          onChange={(e) => setOwnerRef(e.target.value)}
          options={ownerOptions}
          {...maybe("error", save.fieldErrors.owner_ref)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Phase"
          value={phase}
          onChange={(e) => setPhase(e.target.value as ProjectPhase)}
          options={PHASE_OPTIONS}
          {...maybe("error", save.fieldErrors.phase)}
        />
        <Select
          label="Health"
          value={health}
          onChange={(e) => setHealth(e.target.value as ProjectHealth)}
          options={HEALTH_OPTIONS}
          {...maybe("error", save.fieldErrors.health)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label="Progress (%)"
          inputMode="numeric"
          value={progress}
          onChange={(e) => setProgress(e.target.value)}
          {...maybe("error", errors.progress_pct ?? save.fieldErrors.progress_pct)}
        />
        <TextField
          label="Start date"
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
          required
          {...maybe("error", errors.start_date ?? save.fieldErrors.start_date)}
        />
        <TextField
          label="Target date"
          type="date"
          value={targetDate}
          onChange={(e) => setTargetDate(e.target.value)}
          required
          {...maybe("error", errors.target_date ?? save.fieldErrors.target_date)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label={`Budget (${currency || "INR"})`}
          inputMode="decimal"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          helper="Major units — converted to minor units for the server."
          {...maybe("error", errors.budget_minor ?? save.fieldErrors.budget_minor)}
        />
        <TextField
          label={`Spent (${currency || "INR"})`}
          inputMode="decimal"
          value={spent}
          onChange={(e) => setSpent(e.target.value)}
          {...maybe("error", errors.spent_minor ?? save.fieldErrors.spent_minor)}
        />
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          {...maybe("error", errors.currency ?? save.fieldErrors.currency)}
        />
      </div>

      <TextField
        label="Team size"
        inputMode="numeric"
        value={teamSize}
        onChange={(e) => setTeamSize(e.target.value)}
        {...maybe("error", errors.team_size ?? save.fieldErrors.team_size)}
        />

      <TextArea
        label="Summary"
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        optional
        maxLength={2000}
        counterFrom={1800}
        {...maybe("error", save.fieldErrors.summary)}
        />
    </FormDialog>
  );
}
