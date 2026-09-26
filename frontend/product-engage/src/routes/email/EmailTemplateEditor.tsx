import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, fieldErrorMap, useAsync, useCan } from "@opsgrids/client";
import { EmptyState, MailIcon, useAnnounce } from "@opsgrids/ui";

import {
  TEMPLATE_CODE_MAX,
  TEMPLATE_CODE_PATTERN,
  TEMPLATE_SUBJECT_MAX,
  listEmailTemplates,
  saveEmailTemplate,
} from "@/api/email";
import type { EmailTemplateOut } from "@/api/email";
import { failureMessage } from "@/lib/errors";
import { useRouteNotice } from "@/lib/useRouteNotice";
import { Announcer, Button, Card, InlineAlert, PageHeading, Skeleton, TextArea, TextField } from "@/ui";

import { insertAtSelection, placeholderToken, placeholdersIn } from "./templateText";
import { TemplatePreview } from "./TemplatePreview";
import { TemplatesFailure } from "./TemplatesFailure";
import { VariablesPanel } from "./VariablesPanel";

/**
 * Create or edit one email template — `PUT /notifications/templates`, which creates the template or
 * replaces the one with the same code.
 *
 * What is here is what the service stores: a code, a subject, a body and a description. The design
 * also has a category, a preview text, a block designer and an HTML mode; the service has nowhere to
 * keep any of them, and a field whose value is thrown away on save is worse than no field. The body
 * is plain text because the service sends a template's body as the message's text part.
 *
 * There is no test-send: the service has no template test route, only sender and campaign tests.
 */

const WRITE = "notification.delivery.template.write";

type Field = "code" | "subject" | "body" | "description";
type Errors = Partial<Record<Field, string>>;
type Target = "subject" | "body";

/**
 * `taken` is the name of every template that already exists. Saving is an upsert keyed on the name,
 * so a new template that reuses one would silently replace a live template that automations and
 * campaigns send; the names are compared without regard to case so a legacy `Welcome_Email` is not
 * shadowed by `welcome_email`.
 */
function validate(values: Record<Field, string>, editing: boolean, taken: string[]): Errors {
  const errors: Errors = {};
  const code = values.code.trim();
  if (!editing) {
    if (code === "") errors.code = "Give the template a name.";
    else if (!TEMPLATE_CODE_PATTERN.test(code)) {
      errors.code = "Use lowercase letters, digits and underscores only.";
    } else if (code.length > TEMPLATE_CODE_MAX) {
      errors.code = `Keep the name to ${TEMPLATE_CODE_MAX} characters or fewer.`;
    } else if (taken.some((existing) => existing.toLowerCase() === code.toLowerCase())) {
      errors.code = `A template named ${code} already exists. Choose a different name, or open it from the list to edit it.`;
    }
  }
  if (values.subject.trim() === "") errors.subject = "Every email needs a subject.";
  else if (values.subject.length > TEMPLATE_SUBJECT_MAX) {
    errors.subject = `Keep the subject to ${TEMPLATE_SUBJECT_MAX} characters or fewer.`;
  }
  if (values.body.trim() === "") errors.body = "The body can't be empty.";
  return errors;
}

/** Field problems the service attached to a field this form has, and whatever else it said. */
function serverErrors(error: unknown): { fields: Errors; rest: string | null } {
  if (!(error instanceof ApiError)) return { fields: {}, rest: failureMessage(error) };
  const map = fieldErrorMap(error.problem);
  const fields: Errors = {};
  for (const key of ["code", "subject", "body", "description"] as const) {
    const message = map[key];
    if (message !== undefined) fields[key] = message;
  }
  return { fields, rest: Object.keys(fields).length > 0 ? null : failureMessage(error) };
}

const UNCHECKED = "Couldn't check which template names are already in use. Try saving again in a moment.";

function TemplateForm({
  initial,
  known,
  taken,
  onRecheck,
}: {
  initial: EmailTemplateOut | null;
  /** Placeholders the organization's other email templates use. */
  known: string[];
  /** Names of the templates that exist, or null while that list is not available. */
  taken: string[] | null;
  /** Ask for the list of existing names again. */
  onRecheck: () => void;
}) {
  const navigate = useNavigate();
  const announce = useAnnounce();
  const canWrite = useCan(WRITE, "ORG");
  const editing = initial !== null;

  const [code, setCode] = useState(initial?.id ?? "");
  const [subject, setSubject] = useState(initial?.subject ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [added, setAdded] = useState<string[]>([]);
  const [errors, setErrors] = useState<Errors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const codeRef = useRef<HTMLInputElement>(null);
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const target = useRef<Target>("body");
  const pendingCaret = useRef<{ field: Target; caret: number } | null>(null);

  /* After an insertion the field has re-rendered with its new value; only now can the caret be
     placed after the inserted token and focus handed back, so typing carries on where it was. */
  useLayoutEffect(() => {
    const pending = pendingCaret.current;
    if (pending === null) return;
    pendingCaret.current = null;
    const element = pending.field === "subject" ? subjectRef.current : bodyRef.current;
    element?.focus();
    element?.setSelectionRange(pending.caret, pending.caret);
  }, [subject, body]);

  const variables = useMemo(() => {
    const all = [...placeholdersIn(`${subject}\n${body}`), ...known, ...added];
    return [...new Set(all)];
  }, [subject, body, known, added]);

  function insertVariable(name: string) {
    const field = target.current;
    const element = field === "subject" ? subjectRef.current : bodyRef.current;
    const value = field === "subject" ? subject : body;
    const start = element?.selectionStart ?? value.length;
    const end = element?.selectionEnd ?? value.length;
    const next = insertAtSelection(value, start, end, placeholderToken(name));
    pendingCaret.current = { field, caret: next.caret };
    if (field === "subject") setSubject(next.value);
    else setBody(next.value);
    setAdded((current) => (current.includes(name) ? current : [...current, name]));
    setSaved(false);
    clearError(field);
  }

  function clearError(field: Field) {
    setErrors((current) => {
      if (current[field] === undefined) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  function edit(field: Field, set: (value: string) => void, value: string) {
    set(value);
    setSaved(false);
    clearError(field);
  }

  async function save() {
    if (saving || !canWrite) return;
    const found = validate({ code, subject, body, description }, editing, taken ?? []);
    setErrors(found);
    setFailure(null);
    const invalid = (["code", "subject", "body"] as const).find((field) => found[field] !== undefined);
    if (invalid !== undefined) {
      // A field error is only described, so it is read when focus reaches the field: say that the
      // save did not happen, and take focus to the first field that needs attention.
      announce(`Template not saved. ${found[invalid]}`);
      const refs = { code: codeRef, subject: subjectRef, body: bodyRef };
      refs[invalid].current?.focus();
      return;
    }
    if (!editing && taken === null) {
      // Without the list of names there is no telling a new template from one that would replace a
      // live one, so the save waits until the names can be checked.
      setFailure(UNCHECKED);
      onRecheck();
      return;
    }

    setSaving(true);
    try {
      const result = await saveEmailTemplate({
        code: code.trim(),
        channel: "email",
        subject: subject.trim(),
        body,
        ...(description.trim() === "" ? {} : { description: description.trim() }),
      });
      if (editing) {
        setSaved(true);
        announce(`${result.id} saved.`);
      } else {
        navigate(`/email/templates/${encodeURIComponent(result.id)}`, {
          replace: true,
          state: { notice: `${result.id} created.` },
        });
      }
    } catch (caught) {
      // Everything typed stays where it is: a refusal is a reason to correct a field, not to
      // start the form again.
      const { fields, rest } = serverErrors(caught);
      setErrors(fields);
      setFailure(rest);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-4">
      <form
        noValidate
        className="flex flex-col gap-6 lg:col-span-3"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <TextField
            label="Template Name"
            required
            ref={codeRef}
            value={code}
            readOnly={editing || !canWrite}
            onChange={(event) => edit("code", setCode, event.target.value)}
            error={errors.code}
            helper={
              editing
                ? "The name is the template's identifier, so it can't change."
                : "Lowercase letters, digits and underscores, like order_shipped."
            }
          />
          <TextField
            label="Subject"
            required
            ref={subjectRef}
            value={subject}
            readOnly={!canWrite}
            onFocus={() => {
              target.current = "subject";
            }}
            onChange={(event) => edit("subject", setSubject, event.target.value)}
            error={errors.subject}
          />
          <TextField
            label="Description"
            optional
            value={description}
            readOnly={!canWrite}
            onChange={(event) => edit("description", setDescription, event.target.value)}
            error={errors.description}
            helper="For your team. Recipients never see it."
          />
          <TextArea
            label="Body"
            required
            ref={bodyRef}
            rows={12}
            value={body}
            readOnly={!canWrite}
            onFocus={() => {
              target.current = "body";
            }}
            onChange={(event) => edit("body", setBody, event.target.value)}
            error={errors.body}
            helper="Plain text. Write {{customer_name}} wherever a value should be filled in."
          />
          <InlineAlert tone="info" title="Unsubscribe footer" live="off">
            An unsubscribe link is added automatically to campaign emails only. A message sent from
            a template goes out exactly as written here.
          </InlineAlert>
        </Card>

        <TemplatePreview subject={subject} body={body} />

        <div aria-live="polite" className="flex flex-col gap-3">
          {failure !== null ? <InlineAlert tone="danger" title="Couldn't save the template">{failure}</InlineAlert> : null}
          {saved ? <InlineAlert tone="success" title="Template saved" /> : null}
          {!canWrite ? (
            <InlineAlert tone="info" title="You can read this template but not change it">
              Editing needs the template write permission. Ask someone who manages roles in your
              organization to add it.
            </InlineAlert>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          {canWrite ? (
            <Button type="submit" loading={saving}>
              Save Template
            </Button>
          ) : null}
          <Button variant="secondary" onClick={() => navigate("/email/templates")}>
            Back to templates
          </Button>
        </div>
      </form>

      <aside className="lg:col-span-1">
        <Card className="p-4">
          <VariablesPanel variables={variables} onInsert={insertVariable} />
        </Card>
      </aside>
    </div>
  );
}

function EditorLoading() {
  return (
    <>
      <div aria-live="polite" className="sr-only">
        Loading template
      </div>
      <Card className="flex flex-col gap-4 p-6">
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-48 w-full rounded-tile" />
      </Card>
    </>
  );
}

function Editor() {
  const { id } = useParams();
  const titleRef = useRef<HTMLHeadingElement>(null);
  useRouteNotice(titleRef);
  const editing = id !== undefined;

  const state = useAsync(listEmailTemplates, []);
  const templates = state.status === "success" ? state.data.items : null;
  const current = useMemo(
    () => (templates === null || id === undefined ? null : (templates.find((t) => t.id === id) ?? null)),
    [templates, id],
  );
  const taken = useMemo(() => (templates === null ? null : templates.map((t) => t.id)), [templates]);
  const known = useMemo(
    () => [...new Set((templates ?? []).filter((t) => t.id !== id).flatMap((t) => t.placeholders))],
    [templates, id],
  );

  // A new template does not wait for the list: it only feeds the variable suggestions.
  const heading = (
    <PageHeading
      ref={titleRef}
      eyebrow="Email"
      title={editing ? "Edit Template" : "New Template"}
      description="Design your email template with variables and preview."
    />
  );

  if (editing && state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        {heading}
        <EditorLoading />
      </div>
    );
  }
  if (editing && state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        {heading}
        <TemplatesFailure error={state.error} onRetry={state.reload} />
      </div>
    );
  }
  if (editing && current === null) {
    return (
      <div className="rise flex flex-col gap-6">
        {heading}
        <Card className="p-6">
          <EmptyState
            icon={<MailIcon className="size-6" />}
            title="That template doesn't exist"
            description="It may have been renamed, or the link is out of date."
            action={
              <Link
                to="/email/templates"
                className="text-sm font-semibold text-accent-ink underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Back to templates
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      {heading}
      <TemplateForm key={id ?? "new"} initial={current} known={known} taken={taken} onRecheck={state.reload} />
    </div>
  );
}

export function EmailTemplateEditorPage() {
  return (
    <Announcer>
      <Editor />
    </Announcer>
  );
}
