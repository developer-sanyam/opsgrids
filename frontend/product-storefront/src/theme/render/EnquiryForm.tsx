import { useId, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { submitEnquiry } from "@/api/client";
import type { EnquiryInput } from "@/api/client";
import { ActionButton, StoreHeading, StoreText } from "./primitives";
import { bool, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

type Errors = Partial<Record<keyof EnquiryInput, string>>;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;

const EMPTY: EnquiryInput = {
  name: "",
  company: "",
  email: "",
  phone: "",
  requirement: "",
  quantity: "",
  gstin: "",
};

/**
 * Client validation that mirrors the server's rules — it never replaces them. The same document is
 * re-checked by `submitEnquiry`, which is what the real endpoint will do; this pass only saves the
 * buyer a round trip.
 */
function validate(values: EnquiryInput, askGstin: boolean): Errors {
  const errors: Errors = {};
  if (values.name.trim() === "") errors.name = "Enter your name.";
  if (values.company.trim() === "") errors.company = "Enter your company name.";
  if (values.email.trim() === "") errors.email = "Enter an email address we can reply to.";
  else if (!EMAIL.test(values.email.trim())) errors.email = "That email address doesn't look right.";
  if (values.requirement.trim() === "") errors.requirement = "Tell us which line you need.";
  if (askGstin && values.gstin.trim() !== "" && !GSTIN.test(values.gstin.trim().toUpperCase()))
    errors.gstin = "A GSTIN is 15 characters, like 27AABCU9603R1ZX.";
  return errors;
}

const fieldStyle = {
  fontFamily: T.fontBody,
  fontSize: "0.9375rem",
  color: T.text,
  background: T.bg,
  border: `1px solid ${T.border}`,
  borderRadius: T.radius,
  padding: step(0.6),
  width: "100%",
} as const;

/** The buyer's way in. Every field is labelled, every error is tied to its input. */
export function EnquiryForm({ settings }: SectionRenderProps) {
  const base = useId();
  const [values, setValues] = useState<EnquiryInput>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const askQuantity = bool(settings, "showQuantity", true);
  const askGstin = bool(settings, "showGstin", true);
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const consent = optional(settings, "consentText");

  function set<K extends keyof EnquiryInput>(key: K, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Guard the second click: a submit already in flight ignores the next one.
    if (submitting) return;
    const found = validate(values, askGstin);
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    try {
      await submitEnquiry(values);
      setDone(true);
      setValues(EMPTY);
    } catch (error) {
      // The entered data stays exactly where it was — a failed send must never cost the buyer
      // the paragraph they just typed.
      setFailure(error instanceof Error ? error.message : "That didn't send. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function field(key: keyof EnquiryInput, label: string, type: "text" | "email" | "tel" | "textarea") {
    const id = `${base}-${key}`;
    const errorId = `${id}-error`;
    const error = errors[key];
    const shared = {
      id,
      name: key,
      value: values[key],
      "aria-invalid": error ? true : undefined,
      "aria-describedby": error ? errorId : undefined,
      style: { ...fieldStyle, borderColor: error ? T.accent : T.border },
      onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        set(key, event.target.value),
    };
    return (
      <div key={key} style={{ display: "flex", flexDirection: "column", gap: step(0.25) }}>
        <label htmlFor={id} style={{ fontFamily: T.fontBody, fontSize: "0.8125rem", fontWeight: 600, color: T.text }}>
          {label}
        </label>
        {type === "textarea" ? <textarea rows={3} {...shared} /> : <input type={type} {...shared} />}
        {/* `ThemeTokens` has no semantic colour — a theme names one accent and nothing else — so an
            error is marked by weight, position and wording, never by a tint that would read as
            "accent" in one theme and "danger" in another. */}
        {error ? (
          <span id={errorId} style={{ fontFamily: T.fontBody, fontSize: "0.75rem", color: T.text, fontWeight: 700 }}>
            {error}
          </span>
        ) : null}
      </div>
    );
  }

  return (
    <section
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        padding: step(1.3),
        display: "flex",
        flexDirection: "column",
        gap: step(0.8),
        height: "100%",
      }}
    >
      {heading ? <StoreHeading size="md">{heading}</StoreHeading> : null}
      {body ? (
        <StoreText muted small>
          {body}
        </StoreText>
      ) : null}

      {/* Both outcomes are announced: the region exists before either happens, so a screen reader
          hears the change rather than the region appearing. */}
      <div role="status" aria-live="polite">
        {done ? (
          <StoreText small style={{ color: T.accent, fontWeight: 700 }}>
            {str(settings, "successMessage", "Enquiry received.")}
          </StoreText>
        ) : null}
      </div>
      {failure ? (
        <div role="alert" style={{ borderLeft: `3px solid ${T.accent}`, paddingLeft: step(0.6) }}>
          <StoreText small style={{ fontWeight: 700 }}>
            {failure}
          </StoreText>
        </div>
      ) : null}

      <form onSubmit={onSubmit} noValidate style={{ display: "flex", flexDirection: "column", gap: step(0.6) }}>
        {field("name", "Your name", "text")}
        {field("company", "Company", "text")}
        {field("email", "Email", "email")}
        {field("phone", "Phone (optional)", "tel")}
        {field("requirement", "What do you need?", "textarea")}
        {askQuantity ? field("quantity", "Quantity (optional)", "text") : null}
        {askGstin ? field("gstin", "GSTIN (optional)", "text") : null}
        <div style={{ marginTop: step(0.3) }}>
          <ActionButton disabled={submitting}>
            {submitting ? "Sending…" : str(settings, "submitLabel", "Send enquiry")}
          </ActionButton>
        </div>
        {consent ? (
          <StoreText muted small>
            {consent}
          </StoreText>
        ) : null}
      </form>
    </section>
  );
}
