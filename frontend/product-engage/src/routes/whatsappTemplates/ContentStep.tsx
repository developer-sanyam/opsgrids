import { useRef } from "react";

import { PlusIcon } from "@opsgrids/ui";

import type { TemplateButtonType } from "@/api/whatsappTemplates";
import { Button, Select, TextArea, TextField } from "@/ui";
import type { SelectOption } from "@/ui";

import { BUTTON_TYPE_LABEL } from "./labels";
import {
  alignExamples,
  exampleValues,
  insertVariable,
  LIMITS,
  substitute,
  variableIndexes,
  wrapSelection,
} from "./draft";
import type { Draft, DraftButton, Edit, FieldErrors, HeaderChoice } from "./draft";
import { WhatsAppBubble } from "./WhatsAppBubble";
import type { BubbleHeader } from "./WhatsAppBubble";

const HEADER_OPTIONS: SelectOption[] = [
  { value: "none", label: "No header" },
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "video", label: "Video" },
  { value: "document", label: "Document" },
];

const BUTTON_OPTIONS: SelectOption[] = (Object.keys(BUTTON_TYPE_LABEL) as TemplateButtonType[]).map(
  (type) => ({ value: type, label: BUTTON_TYPE_LABEL[type] }),
);

const FORMATS: { label: string; mark: string; glyph: string }[] = [
  { label: "Bold", mark: "*", glyph: "B" },
  { label: "Italic", mark: "_", glyph: "I" },
  { label: "Strikethrough", mark: "~", glyph: "S" },
  { label: "Code", mark: "```", glyph: "<>" },
];

const NEW_BUTTON: DraftButton = { type: "quick_reply", text: "", url: "", phone: "" };

function previewHeader(draft: Draft): BubbleHeader | undefined {
  if (draft.headerType === "none") return undefined;
  if (draft.headerType === "text") {
    const text = substitute(draft.headerText, { "1": draft.headerExample });
    return text === "" ? undefined : { kind: "text", text };
  }
  return { kind: "media", type: draft.headerType };
}

/** The message as drafted, with the examples typed so far standing in for the variables. */
export function DraftPreview({ draft }: { draft: Draft }) {
  return (
    <WhatsAppBubble
      text={substitute(draft.body, exampleValues(draft.examples))}
      header={previewHeader(draft)}
      footer={draft.footer}
      buttons={draft.buttons.map((button) => ({
        type: button.type,
        text: button.text === "" ? "Button label" : button.text,
      }))}
    />
  );
}

function ButtonRow({
  button,
  position,
  errors,
  onChange,
  onRemove,
}: {
  button: DraftButton;
  position: number;
  errors: FieldErrors;
  onChange: (next: DraftButton) => void;
  onRemove: () => void;
}) {
  const key = `components.buttons.${position}`;
  const number = position + 1;
  return (
    <li className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Select
          label={`Button ${number} type`}
          value={button.type}
          options={BUTTON_OPTIONS}
          onChange={(event) => onChange({ ...button, type: event.target.value as TemplateButtonType })}
        />
        <TextField
          label={`Button ${number} label`}
          value={button.text}
          onChange={(event) => onChange({ ...button, text: event.target.value })}
          autoComplete="off"
          {...(errors[`${key}.text`] !== undefined ? { error: errors[`${key}.text`] } : {})}
          helper={`${button.text.length} / ${LIMITS.buttonText} characters`}
        />
      </div>
      {button.type === "url" ? (
        <TextField
          label={`Button ${number} link`}
          value={button.url}
          onChange={(event) => onChange({ ...button, url: event.target.value })}
          placeholder="https://example.com/track"
          autoComplete="off"
          spellCheck={false}
          {...(errors[`${key}.url`] !== undefined ? { error: errors[`${key}.url`] } : {})}
        />
      ) : null}
      {button.type === "phone" ? (
        <TextField
          label={`Button ${number} phone number`}
          value={button.phone}
          onChange={(event) => onChange({ ...button, phone: event.target.value })}
          placeholder="+919876543210"
          autoComplete="off"
          spellCheck={false}
          {...(errors[`${key}.phone`] !== undefined ? { error: errors[`${key}.phone`] } : {})}
        />
      ) : null}
      <div>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={`Remove button ${number}`}>
          Remove button
        </Button>
      </div>
    </li>
  );
}

/**
 * Step 2: what the message says. Header, message, footer and buttons are the four parts Meta's
 * template format has; each maps onto one field of the create request, and each error is looked
 * up under the same dotted path the service uses for its own refusals.
 */
export function ContentStep({
  draft,
  errors,
  onChange,
}: {
  draft: Draft;
  errors: FieldErrors;
  onChange: (patch: Partial<Draft>) => void;
}) {
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const indexes = variableIndexes(draft.body);
  const examples = alignExamples(draft.examples, indexes.length);
  const headerHasVariable = draft.headerType === "text" && variableIndexes(draft.headerText).length > 0;

  function applyEdit(make: (value: string, start: number, end: number) => Edit) {
    const field = bodyRef.current;
    const start = field?.selectionStart ?? draft.body.length;
    const end = field?.selectionEnd ?? draft.body.length;
    const edit = make(draft.body, start, end);
    onChange({ body: edit.value });
    requestAnimationFrame(() => {
      field?.focus();
      field?.setSelectionRange(edit.selection[0], edit.selection[1]);
    });
  }

  function setExample(position: number, value: string) {
    const next = [...examples];
    next[position] = value;
    onChange({ examples: next });
  }

  function setButton(position: number, next: DraftButton) {
    onChange({ buttons: draft.buttons.map((button, i) => (i === position ? next : button)) });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-6 lg:col-span-2">
        <fieldset className="flex flex-col gap-3">
          <legend className="text-section text-ink">Header</legend>
          <Select
            label="Header type"
            value={draft.headerType}
            options={HEADER_OPTIONS}
            onChange={(event) => onChange({ headerType: event.target.value as HeaderChoice })}
            helper="Optional. A header sits above the message."
          />
          {draft.headerType === "text" ? (
            <TextField
              label="Header text"
              value={draft.headerText}
              onChange={(event) => onChange({ headerText: event.target.value })}
              autoComplete="off"
              helper={`${draft.headerText.length} / ${LIMITS.headerText} characters. One variable, {{1}}, is allowed.`}
              {...(errors["components.header.text"] !== undefined
                ? { error: errors["components.header.text"] }
                : {})}
            />
          ) : null}
          {headerHasVariable ? (
            <TextField
              label="Example for {{1}} in the header"
              value={draft.headerExample}
              onChange={(event) => onChange({ headerExample: event.target.value })}
              autoComplete="off"
              {...(errors["components.header.example"] !== undefined
                ? { error: errors["components.header.example"] }
                : {})}
            />
          ) : null}
          {draft.headerType !== "none" && draft.headerType !== "text" ? (
            <TextField
              label="Example media link"
              value={draft.headerExample}
              onChange={(event) => onChange({ headerExample: event.target.value })}
              placeholder="https://example.com/sample.jpg"
              autoComplete="off"
              spellCheck={false}
              helper="A link to a sample file, so Meta can review how it looks."
              {...(errors["components.header.example"] !== undefined
                ? { error: errors["components.header.example"] }
                : {})}
            />
          ) : null}
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="text-section text-ink">Message</legend>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div role="group" aria-label="Formatting" className="flex gap-1">
              {FORMATS.map((format) => (
                <Button
                  key={format.label}
                  variant="secondary"
                  size="sm"
                  aria-label={format.label}
                  onClick={() => applyEdit((v, s, e) => wrapSelection(v, s, e, format.mark))}
                >
                  {format.glyph}
                </Button>
              ))}
            </div>
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => applyEdit(insertVariable)}
            >
              Add Variable
            </Button>
          </div>
          <TextArea
            ref={bodyRef}
            label="Message text"
            required
            rows={8}
            value={draft.body}
            onChange={(event) => onChange({ body: event.target.value })}
            className="font-mono"
            helper={`${draft.body.length.toLocaleString()} / ${LIMITS.body.toLocaleString()} characters. Meta reviews the wording. Avoid promotional language in a Utility template or it may be moved to Marketing.`}
            {...(errors["components.body.text"] !== undefined
              ? { error: errors["components.body.text"] }
              : {})}
          />
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="text-section text-ink">Variables</legend>
          {indexes.length === 0 ? (
            <p className="text-body-sm text-muted">
              No variables yet. Use Add Variable to put {"{{1}}"} in the message.
            </p>
          ) : (
            <>
              <p
                className={
                  errors["components.body.examples"] !== undefined
                    ? "text-body-sm font-semibold text-negative"
                    : "text-body-sm text-muted"
                }
              >
                {errors["components.body.examples"] ??
                  "Meta needs an example value for each variable so it can review the message."}
              </p>
              {examples.map((example, position) => (
                <TextField
                  key={indexes[position] ?? position}
                  label={`Example for {{${position + 1}}}`}
                  required
                  value={example}
                  onChange={(event) => setExample(position, event.target.value)}
                  autoComplete="off"
                  {...(errors[`components.body.examples.${position}`] !== undefined
                    ? { error: errors[`components.body.examples.${position}`] }
                    : {})}
                />
              ))}
            </>
          )}
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="text-section text-ink">Footer</legend>
          <TextField
            label="Footer text"
            optional
            value={draft.footer}
            onChange={(event) => onChange({ footer: event.target.value })}
            autoComplete="off"
            helper={`${draft.footer.length} / ${LIMITS.footer} characters`}
            {...(errors["components.footer.text"] !== undefined
              ? { error: errors["components.footer.text"] }
              : {})}
          />
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend className="text-section text-ink">Buttons</legend>
          {errors["components.buttons"] !== undefined ? (
            <p className="text-body-sm font-semibold text-negative">{errors["components.buttons"]}</p>
          ) : (
            <p className="text-body-sm text-muted">
              Optional. Up to {LIMITS.buttons} buttons: quick replies, or a few that open a link or
              call a number.
            </p>
          )}
          {draft.buttons.length > 0 ? (
            <ul className="flex flex-col gap-3">
              {draft.buttons.map((button, position) => (
                <ButtonRow
                  key={position}
                  button={button}
                  position={position}
                  errors={errors}
                  onChange={(next) => setButton(position, next)}
                  onRemove={() =>
                    onChange({ buttons: draft.buttons.filter((_, i) => i !== position) })
                  }
                />
              ))}
            </ul>
          ) : null}
          <div>
            <Button
              variant="secondary"
              leadingIcon={<PlusIcon className="size-4" />}
              disabled={draft.buttons.length >= LIMITS.buttons}
              onClick={() => onChange({ buttons: [...draft.buttons, NEW_BUTTON] })}
            >
              Add button
            </Button>
          </div>
        </fieldset>
      </div>

      <div className="flex flex-col gap-3 lg:col-span-1">
        <h3 className="text-card-title text-ink">Preview</h3>
        <DraftPreview draft={draft} />
        <p className="text-caption text-muted">
          Sample values come from the examples you enter for each variable.
        </p>
      </div>
    </div>
  );
}
