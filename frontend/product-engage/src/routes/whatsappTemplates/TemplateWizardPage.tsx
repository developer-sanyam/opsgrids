import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, fieldErrorMap, useMutation } from "@opsgrids/client";
import { Stepper, StepperActions } from "@opsgrids/ui";
import type { StepItem } from "@opsgrids/ui";

import { createTemplate } from "@/api/whatsappTemplates";
import type { TemplateCategory, WhatsAppTemplate } from "@/api/whatsappTemplates";
import { failureMessage } from "@/lib/errors";
import {
  Announcer,
  Button,
  Card,
  InlineAlert,
  PageHeader,
  Select,
  TemplateStatusTag,
  TextField,
  useAnnounce,
} from "@/ui";
import type { SelectOption } from "@/ui";

import { ContentStep, DraftPreview } from "./ContentStep";
import {
  EMPTY_DRAFT,
  stepOfField,
  toInput,
  validateContent,
  validateDetails,
  validateDraft,
} from "./draft";
import type { Draft, FieldErrors } from "./draft";
import { CATEGORY_LABEL, LANGUAGES, templateName } from "./labels";

/**
 * Create a template in four steps: Details, Content, Review, Submit.
 *
 * **Progress is guarded.** Continue validates the step it leaves, and going forward by any route —
 * Continue, or a step in the list — validates every step it passes, so a step with a problem cannot
 * be skipped. Going back is always free and never loses what was entered: the draft lives here,
 * above the steps, not in them.
 *
 * **Two sources of error, one map.** Local checks mirror Meta's rules so most mistakes are caught
 * before a request is made; the service's own 422 lands in the same dotted-path map and shows on
 * the same control. Local checks never replace the service — a draft that passes can still be
 * refused, and the refusal is shown, with the data kept.
 */

const STEPS: StepItem[] = [
  { id: "details", title: "Details", description: "Name, category and language" },
  { id: "content", title: "Content", description: "Header, message and buttons" },
  { id: "review", title: "Review", description: "Check how it reads" },
  { id: "submit", title: "Submit", description: "Send it to Meta" },
];

const LAST = STEPS.length - 1;
const LIST_PATH = "/whatsapp/templates";

const CATEGORY_OPTIONS: SelectOption[] = (
  Object.keys(CATEGORY_LABEL) as TemplateCategory[]
).map((value) => ({ value, label: CATEGORY_LABEL[value] }));

const LANGUAGE_OPTIONS: SelectOption[] = LANGUAGES.map((language) => ({ ...language }));

/** The errors a step owns: 0 Details, 1 Content, the later steps own none of their own. */
function errorsOfStep(step: number, draft: Draft): FieldErrors {
  if (step === 0) return validateDetails(draft);
  if (step === 1) return validateContent(draft);
  return {};
}

/**
 * The first step before `to` that cannot be left as it is. Every earlier step is checked, not just
 * the one being left, so a step edited after it was passed cannot be carried forward unchecked.
 */
function firstBlockedStep(to: number, draft: Draft): 0 | 1 | null {
  for (const step of [0, 1] as const) {
    if (step < to && Object.keys(errorsOfStep(step, draft)).length > 0) return step;
  }
  return null;
}

function DetailsStep({
  draft,
  errors,
  onChange,
}: {
  draft: Draft;
  errors: FieldErrors;
  onChange: (patch: Partial<Draft>) => void;
}) {
  return (
    <div className="flex max-w-xl flex-col gap-6">
      <TextField
        label="Template Name"
        required
        value={draft.name}
        onChange={(event) => onChange({ name: event.target.value })}
        className="font-mono"
        placeholder="order_shipped"
        autoComplete="off"
        spellCheck={false}
        helper="Lowercase letters, numbers and underscores only. This cannot change after approval."
        {...(errors["name"] !== undefined ? { error: errors["name"] } : {})}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Category"
          required
          value={draft.category}
          options={CATEGORY_OPTIONS}
          onChange={(event) => onChange({ category: event.target.value as TemplateCategory })}
          {...(errors["category"] !== undefined ? { error: errors["category"] } : {})}
        />
        <Select
          label="Language"
          required
          value={draft.language}
          options={LANGUAGE_OPTIONS}
          onChange={(event) => onChange({ language: event.target.value })}
          {...(errors["language"] !== undefined ? { error: errors["language"] } : {})}
        />
      </div>
      <p className="text-caption text-muted">
        Utility templates are for transactions the customer already started. Marketing templates
        need marketing opt-in and cost more per message.
      </p>
    </div>
  );
}

function Summary({ draft }: { draft: Draft }) {
  const rows: [string, string][] = [
    ["Name", draft.name],
    ["Category", CATEGORY_LABEL[draft.category]],
    ["Language", LANGUAGES.find((l) => l.value === draft.language)?.label ?? draft.language],
    ["Buttons", String(draft.buttons.length)],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-body-sm">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted">{term}</dt>
          <dd className="break-words font-semibold text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Where a problem is fixed: the step that owns the field, as a button that goes there. */
function ProblemList({
  errors,
  onFix,
}: {
  errors: FieldErrors;
  onFix: (step: 0 | 1) => void;
}) {
  const entries = Object.entries(errors).filter(
    // The per-example entries repeat the aggregate line above them.
    ([field]) => !/^components\.body\.examples\.\d+$/.test(field) || errors["components.body.examples"] === undefined,
  );
  return (
    <ul className="flex flex-col gap-2">
      {entries.map(([field, message]) => {
        const step = stepOfField(field);
        return (
          <li key={field} className="flex flex-wrap items-center gap-2 text-body-sm text-ink">
            <span>{message}</span>
            {step !== null ? (
              <Button variant="ghost" size="sm" onClick={() => onFix(step)}>
                {step === 0 ? "Fix in Details" : "Fix in Content"}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function ReviewStep({ draft, onFix }: { draft: Draft; onFix: (step: 0 | 1) => void }) {
  const problems = validateDraft(draft);
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <Summary draft={draft} />
        {Object.keys(problems).length === 0 ? (
          <InlineAlert tone="success" live="off" title="Ready to submit">
            Everything here passes the checks made before sending. Meta still reviews the wording.
          </InlineAlert>
        ) : (
          <InlineAlert tone="warning" live="off" title="Fix these before you submit">
            <ProblemList errors={problems} onFix={onFix} />
          </InlineAlert>
        )}
      </div>
      <div className="flex flex-col gap-3 lg:col-span-1">
        <h3 className="text-card-title text-ink">Preview</h3>
        <DraftPreview draft={draft} />
      </div>
    </div>
  );
}

function SubmitStep({
  draft,
  failure,
  serverErrors,
  onFix,
}: {
  draft: Draft;
  failure: string | null;
  serverErrors: FieldErrors;
  onFix: (step: 0 | 1) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <p className="text-body text-ink">
          Submitting sends {draft.name === "" ? "this template" : `“${draft.name}”`} to Meta for
          review. Meta still has to approve it before it can send outside a customer's 24-hour
          service window, so its status starts as pending.
        </p>
        {failure !== null ? (
          <InlineAlert tone="danger" title="That template wasn't submitted">
            <p>{failure}</p>
            {Object.keys(serverErrors).length > 0 ? (
              <div className="mt-2">
                <ProblemList errors={serverErrors} onFix={onFix} />
              </div>
            ) : null}
            <p className="mt-2">Nothing you entered was lost.</p>
          </InlineAlert>
        ) : null}
      </div>
      <div className="flex flex-col gap-3 lg:col-span-1">
        <h3 className="text-card-title text-ink">Preview</h3>
        <DraftPreview draft={draft} />
      </div>
    </div>
  );
}

function Submitted({ template, onOpen, onList }: { template: WhatsAppTemplate; onOpen: () => void; onList: () => void }) {
  return (
    <div className="flex flex-col items-start gap-4">
      <InlineAlert tone="success" title="Template submitted">
        {templateName(template)} is with Meta for review. It stays pending until Meta answers, and
        this screen will show the answer once a sync or Meta's own notice brings it in.
      </InlineAlert>
      <div className="flex items-center gap-3">
        <span className="text-body-sm text-muted">Status</span>
        <TemplateStatusTag status={template.status} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onOpen}>View template</Button>
        <Button variant="secondary" onClick={onList}>
          Back to templates
        </Button>
      </div>
    </div>
  );
}

function TemplateWizard() {
  const navigate = useNavigate();
  const announce = useAnnounce();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);
  /** Steps whose local errors are on show: a step is quiet until someone tries to leave it. */
  const [shown, setShown] = useState<ReadonlySet<number>>(new Set());
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [created, setCreated] = useState<WhatsAppTemplate | null>(null);

  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    announce(`Step ${step + 1} of ${STEPS.length}: ${STEPS[step]?.title ?? ""}`);
  }, [step, announce]);

  const create = useMutation(createTemplate, {
    onSuccess: (template) => {
      setCreated(template);
      announce(`${templateName(template)} submitted. Status: ${template.status}.`);
    },
    onError: (error) => {
      setFailure(failureMessage(error));
      const found = error instanceof ApiError ? fieldErrorMap(error.problem) : {};
      setServerErrors(found);
      const owners = Object.keys(found)
        .map(stepOfField)
        .filter((owner): owner is 0 | 1 => owner !== null);
      if (owners.length > 0) {
        // Take the user to the first step the service found fault with, data intact.
        const owner = Math.min(...owners);
        setStep(owner);
        setShown((current) => new Set(current).add(owner));
      }
    },
  });

  function update(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }));
    // An edit answers the service's complaint about this step's fields, and only those.
    setServerErrors((current) => {
      const kept = Object.entries(current).filter(([field]) => stepOfField(field) !== step);
      return kept.length === Object.keys(current).length ? current : Object.fromEntries(kept);
    });
  }

  function goTo(target: number) {
    const blocked = target > step ? firstBlockedStep(target, draft) : null;
    if (blocked !== null) {
      setStep(blocked);
      setShown((current) => new Set(current).add(blocked));
      return;
    }
    setStep(target);
    setReached((current) => Math.max(current, target));
  }

  function fix(target: 0 | 1) {
    setShown((current) => new Set(current).add(target));
    setStep(target);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (step < LAST) {
      goTo(step + 1);
      return;
    }
    const blocked = firstBlockedStep(2, draft);
    if (blocked !== null) {
      fix(blocked);
      return;
    }
    setFailure(null);
    void create.mutate(toInput(draft));
  }

  const local = shown.has(step) ? errorsOfStep(step, draft) : {};
  const errors: FieldErrors = { ...serverErrors, ...local };
  const hasErrors = (index: number) =>
    (shown.has(index) && Object.keys(errorsOfStep(index, draft)).length > 0) ||
    Object.keys(serverErrors).some((field) => stepOfField(field) === index);
  const invalidSteps = STEPS.map((item, index) => ({ ...item, invalid: hasErrors(index) }));
  const serverHere = Object.keys(serverErrors).some((field) => stepOfField(field) === step);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="WhatsApp"
        title="Create WhatsApp Template"
        description="Create a new template and submit it for Meta approval."
        actions={
          <Button variant="secondary" onClick={() => navigate(LIST_PATH)} disabled={create.pending}>
            Cancel
          </Button>
        }
      />

      <Card className="grid grid-cols-1 gap-6 p-6 md:grid-cols-4">
        {created !== null ? (
          <div className="md:col-span-4">
            <Submitted
              template={created}
              onOpen={() => navigate(`${LIST_PATH}/${encodeURIComponent(created.id)}`)}
              onList={() => navigate(LIST_PATH)}
            />
          </div>
        ) : (
          <>
            <Stepper
              steps={invalidSteps}
              current={step}
              reached={reached}
              onStepChange={goTo}
              label="Progress"
              className="md:col-span-1"
            />
            <form noValidate onSubmit={onSubmit} className="flex flex-col gap-6 md:col-span-3">
              <h2 className="text-section text-ink">
                {["Template Details", "Template Content", "Review", "Submit to Meta"][step]}
              </h2>
              {serverHere && failure !== null ? (
                <InlineAlert tone="danger" title="That template wasn't submitted">
                  {failure} Nothing you entered was lost.
                </InlineAlert>
              ) : hasErrors(step) ? (
                <InlineAlert tone="warning" title="Check the fields marked below">
                  Nothing has been sent yet.
                </InlineAlert>
              ) : null}
              {step === 0 ? <DetailsStep draft={draft} errors={errors} onChange={update} /> : null}
              {step === 1 ? <ContentStep draft={draft} errors={errors} onChange={update} /> : null}
              {step === 2 ? <ReviewStep draft={draft} onFix={fix} /> : null}
              {step === 3 ? (
                <SubmitStep draft={draft} failure={failure} serverErrors={serverErrors} onFix={fix} />
              ) : null}
              <StepperActions
                {...(step > 0 ? { onBack: () => goTo(step - 1) } : {})}
                continueLabel={
                  step === 0 ? "Next: Content" : step === 1 ? "Next: Review" : step === 2 ? "Next: Submit" : "Submit for review"
                }
                final={step === LAST}
                pending={create.pending}
                pendingLabel="Submitting…"
              />
            </form>
          </>
        )}
      </Card>
    </div>
  );
}

export function TemplateWizardPage() {
  return (
    <Announcer>
      <TemplateWizard />
    </Announcer>
  );
}
