import type {
  CreateTemplateInput,
  TemplateButton,
  TemplateButtonType,
  TemplateCategory,
  TemplateComponentsInput,
  TemplateHeaderType,
} from "@/api/whatsappTemplates";

/**
 * The create flow's form state, the local mirror of Meta's template rules, and the substitution
 * the live preview uses. Pure: no React, no I/O, so every rule is testable on its own.
 *
 * **Errors are keyed the way the service keys them** — `name`, `components.body.text`,
 * `components.buttons.0.url` — because a 422's `errors[].field` is the same dotted path. One map
 * therefore carries a local error and a server error, and a control looks itself up once.
 *
 * Client validation mirrors the service and never replaces it: a draft that passes here can still
 * be refused, and the refusal is shown on the same control.
 */

export const LIMITS = {
  name: 512,
  headerText: 60,
  body: 1024,
  footer: 60,
  buttonText: 25,
  url: 2000,
  buttons: 10,
  urlButtons: 2,
  phoneButtons: 1,
} as const;

export const NAME_PATTERN = /^[a-z0-9_]+$/;
const PHONE_PATTERN = /^\+[1-9]\d{6,14}$/;
const PLACEHOLDER = /\{\{([^{}]*)\}\}/g;

export type HeaderChoice = "none" | TemplateHeaderType;

export interface DraftButton {
  type: TemplateButtonType;
  text: string;
  url: string;
  phone: string;
}

export interface Draft {
  name: string;
  category: TemplateCategory;
  language: string;
  headerType: HeaderChoice;
  headerText: string;
  /** The header variable's example when the header is text, the sample media URL otherwise. */
  headerExample: string;
  body: string;
  /** `examples[i]` is the example for `{{i + 1}}`. */
  examples: string[];
  footer: string;
  buttons: DraftButton[];
}

export const EMPTY_DRAFT: Draft = {
  name: "",
  category: "utility",
  language: "en_US",
  headerType: "none",
  headerText: "",
  headerExample: "",
  body: "",
  examples: [],
  footer: "",
  buttons: [],
};

export type FieldErrors = Record<string, string>;

/* ------------------------------ placeholders ------------------------------ */

/** The distinct numbers of the `{{n}}` placeholders in `text`, ascending. Malformed ones are not here. */
export function variableIndexes(text: string): number[] {
  const found = new Set<number>();
  for (const match of text.matchAll(PLACEHOLDER)) {
    const inner = match[1] ?? "";
    if (/^\d+$/.test(inner)) found.add(Number(inner));
  }
  return [...found].sort((a, b) => a - b);
}

/** Everything inside `{{ }}` that is not a plain number — `{{name}}`, `{{ 1 }}`. */
function malformedPlaceholders(text: string): string[] {
  const bad: string[] = [];
  for (const match of text.matchAll(PLACEHOLDER)) {
    const inner = match[1] ?? "";
    if (!/^\d+$/.test(inner)) bad.push(match[0]);
  }
  return bad;
}

/** `null` when 1..n are all present, otherwise the sentence saying how the numbering is broken. */
function numberingProblem(indexes: number[]): string | null {
  const expected = indexes.map((_, position) => position + 1);
  if (indexes.every((value, position) => value === expected[position])) return null;
  const wanted = expected.map((n) => `{{${n}}}`).join(", ");
  const found = indexes.map((n) => `{{${n}}}`).join(", ");
  return `Number variables in order with no gaps, starting at {{1}}: you have ${found}, so they should be ${wanted}.`;
}

/** The next number for "Add variable": one past the highest already in the body. */
export function nextVariableIndex(text: string): number {
  const indexes = variableIndexes(text);
  return (indexes[indexes.length - 1] ?? 0) + 1;
}

/**
 * Put `values` into `text`: `{{1}}` becomes `values["1"]`. A placeholder with no value, or a blank
 * one, stays as written — so a half-filled preview shows what is still missing rather than a hole.
 */
export function substitute(text: string, values: Readonly<Record<string, string>>): string {
  return text.replace(PLACEHOLDER, (whole, inner: string) => {
    const value = values[inner];
    return value !== undefined && value.trim() !== "" ? value : whole;
  });
}

/** `{ "1": examples[0], … }` for a body, ready for `substitute`. */
export function exampleValues(examples: readonly string[]): Record<string, string> {
  const values: Record<string, string> = {};
  examples.forEach((example, position) => {
    values[String(position + 1)] = example;
  });
  return values;
}

/** `examples` resized to the body's variable count, keeping what was typed. */
export function alignExamples(examples: readonly string[], count: number): string[] {
  return Array.from({ length: count }, (_, position) => examples[position] ?? "");
}

/* -------------------------------- validation -------------------------------- */

export function validateDetails(draft: Draft): FieldErrors {
  const errors: FieldErrors = {};
  const name = draft.name;
  if (name.trim() === "") errors["name"] = "Enter a template name.";
  else if (!NAME_PATTERN.test(name)) {
    errors["name"] = "Use lowercase letters, numbers and underscores only, like order_shipped.";
  } else if (name.length > LIMITS.name) {
    errors["name"] = `Keep the name to ${LIMITS.name} characters or fewer.`;
  }
  if (draft.language.trim() === "") errors["language"] = "Choose a language.";
  return errors;
}

function validateHeader(draft: Draft, errors: FieldErrors): void {
  if (draft.headerType === "none") return;
  if (draft.headerType === "text") {
    const text = draft.headerText;
    const indexes = variableIndexes(text);
    if (text.trim() === "") errors["components.header.text"] = "Enter the header text.";
    else if (text.length > LIMITS.headerText) {
      errors["components.header.text"] = `Keep the header to ${LIMITS.headerText} characters or fewer.`;
    } else if (malformedPlaceholders(text).length > 0) {
      errors["components.header.text"] = "Use a numbered variable like {{1}}.";
    } else if (indexes.length > 1 || (indexes[0] !== undefined && indexes[0] !== 1)) {
      errors["components.header.text"] = "A header can hold one variable, and it must be {{1}}.";
    } else if (indexes.length === 1 && draft.headerExample.trim() === "") {
      errors["components.header.example"] = "Enter an example value for {{1}} in the header.";
    }
    return;
  }
  if (draft.headerExample.trim() === "") {
    errors["components.header.example"] = "Enter a sample link to an example file.";
  }
}

function validateButton(button: DraftButton, position: number, errors: FieldErrors): void {
  const key = `components.buttons.${position}`;
  if (button.text.trim() === "") errors[`${key}.text`] = "Enter the button label.";
  else if (button.text.length > LIMITS.buttonText) {
    errors[`${key}.text`] = `Keep the label to ${LIMITS.buttonText} characters or fewer.`;
  }
  if (button.type === "url") {
    if (button.url.trim() === "") errors[`${key}.url`] = "Enter the link this button opens.";
    else if (button.url.length > LIMITS.url) {
      errors[`${key}.url`] = `Keep the link to ${LIMITS.url} characters or fewer.`;
    } else if (!isHttpUrl(button.url)) {
      errors[`${key}.url`] = "Enter a full link starting with https://.";
    }
  }
  if (button.type === "phone" && !PHONE_PATTERN.test(button.phone.trim())) {
    errors[`${key}.phone`] = "Enter the number with its country code, like +919876543210.";
  }
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function validateButtons(draft: Draft, errors: FieldErrors): void {
  const { buttons } = draft;
  if (buttons.length > LIMITS.buttons) {
    errors["components.buttons"] = `A template can have ${LIMITS.buttons} buttons at most.`;
  }
  if (buttons.filter((button) => button.type === "url").length > LIMITS.urlButtons) {
    errors["components.buttons"] = `A template can have ${LIMITS.urlButtons} link buttons at most.`;
  }
  if (buttons.filter((button) => button.type === "phone").length > LIMITS.phoneButtons) {
    errors["components.buttons"] = "A template can have one call button at most.";
  }
  buttons.forEach((button, position) => validateButton(button, position, errors));
}

export function validateContent(draft: Draft): FieldErrors {
  const errors: FieldErrors = {};
  validateHeader(draft, errors);

  const body = draft.body;
  const indexes = variableIndexes(body);
  if (body.trim() === "") errors["components.body.text"] = "Enter the message text.";
  else if (body.length > LIMITS.body) {
    errors["components.body.text"] = `Keep the message to ${LIMITS.body.toLocaleString()} characters or fewer.`;
  } else if (malformedPlaceholders(body).length > 0) {
    errors["components.body.text"] = "Use numbered variables like {{1}}, not names.";
  } else {
    const problem = numberingProblem(indexes);
    if (problem !== null) errors["components.body.text"] = problem;
  }

  // Examples are checked against the variables the body has, so a numbering error above does not
  // also become an "examples" error for the same mistake.
  if (errors["components.body.text"] === undefined) {
    for (let position = 0; position < indexes.length; position += 1) {
      if ((draft.examples[position] ?? "").trim() === "") {
        errors[`components.body.examples.${position}`] = `Enter an example for {{${position + 1}}}.`;
      }
    }
    if (Object.keys(errors).some((field) => field.startsWith("components.body.examples."))) {
      errors["components.body.examples"] =
        `Meta needs one example for each of the ${indexes.length} variables.`;
    }
  }

  if (draft.footer.length > LIMITS.footer) {
    errors["components.footer.text"] = `Keep the footer to ${LIMITS.footer} characters or fewer.`;
  } else if (draft.footer.includes("{{")) {
    errors["components.footer.text"] = "A footer cannot contain variables.";
  }

  validateButtons(draft, errors);
  return errors;
}

export function validateDraft(draft: Draft): FieldErrors {
  return { ...validateDetails(draft), ...validateContent(draft) };
}

/** 0 for the Details step, 1 for Content, `null` for a field no step owns. */
export function stepOfField(field: string): 0 | 1 | null {
  if (field === "name" || field === "category" || field === "language") return 0;
  if (field === "components" || field.startsWith("components.")) return 1;
  return null;
}

/* ------------------------------- wire shape ------------------------------- */

function toButton(button: DraftButton): TemplateButton {
  const text = button.text.trim();
  if (button.type === "url") return { type: "url", text, url: button.url.trim() };
  if (button.type === "phone") return { type: "phone", text, phone: button.phone.trim() };
  return { type: "quick_reply", text };
}

/** The request body for a draft that has passed `validateDraft`. Optional parts are left out, not blank. */
export function toInput(draft: Draft): CreateTemplateInput {
  const count = variableIndexes(draft.body).length;
  const components: TemplateComponentsInput = {
    body: { text: draft.body, examples: alignExamples(draft.examples, count).map((e) => e.trim()) },
  };
  if (draft.headerType === "text") {
    const hasVariable = variableIndexes(draft.headerText).length > 0;
    components.header = {
      type: "text",
      text: draft.headerText,
      ...(hasVariable ? { example: draft.headerExample.trim() } : {}),
    };
  } else if (draft.headerType !== "none") {
    components.header = { type: draft.headerType, example: draft.headerExample.trim() };
  }
  if (draft.footer.trim() !== "") components.footer = { text: draft.footer.trim() };
  if (draft.buttons.length > 0) components.buttons = draft.buttons.map(toButton);
  return {
    name: draft.name,
    category: draft.category,
    language: draft.language,
    components,
  };
}

/* ------------------------------ editing helpers ------------------------------ */

export interface Edit {
  value: string;
  /** Where the caret goes after the edit: `[start, end]`. */
  selection: [number, number];
}

/**
 * Wrap the selection in WhatsApp's formatting marks (`*bold*`, `_italic_`, `~strike~`, and three
 * backticks for code). With nothing selected the marks go in empty and the caret between them.
 */
export function wrapSelection(value: string, start: number, end: number, mark: string): Edit {
  const selected = value.slice(start, end);
  const next = `${value.slice(0, start)}${mark}${selected}${mark}${value.slice(end)}`;
  return { value: next, selection: [start + mark.length, start + mark.length + selected.length] };
}

/** Put `{{n}}` — the next number in the body — at the caret, replacing any selection. */
export function insertVariable(value: string, start: number, end: number): Edit {
  const token = `{{${nextVariableIndex(value)}}}`;
  const next = `${value.slice(0, start)}${token}${value.slice(end)}`;
  const caret = start + token.length;
  return { value: next, selection: [caret, caret] };
}
