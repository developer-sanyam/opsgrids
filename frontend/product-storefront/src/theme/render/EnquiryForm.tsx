import { useId, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { submitEnquiry } from "@/api/publicClient";
import type { EnquiryInput } from "@/api/publicClient";
import { useLineList } from "@/store/lineList";
import { ActionButton, StoreHeading, StoreText } from "./primitives";
import { bool, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/** The fields this form's own inputs collect — `store` and `lines` are assembled at submit time
 *  from the section's `storeSlug` setting and the buyer's line list, not typed by hand. */
type FormFields = Omit<EnquiryInput, "store" | "lines" | "whatsappOptIn">;
type Errors = Partial<Record<keyof FormFields, string>>;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// The E.164 rule the WhatsApp consent ledger applies, once separators are stripped.
const WHATSAPP = /^\+[1-9]\d{7,14}$/;
const GSTIN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;

const EMPTY: FormFields = {
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
function validate(values: FormFields, askGstin: boolean, whatsappOptIn: boolean): Errors {
  const errors: Errors = {};
  if (values.name.trim() === "") errors.name = "Enter your name.";
  if (values.company.trim() === "") errors.company = "Enter your company name.";
  if (values.email.trim() === "") errors.email = "Enter an email address we can reply to.";
  else if (!EMAIL.test(values.email.trim())) errors.email = "That email address doesn't look right.";
  if (whatsappOptIn && !WHATSAPP.test(values.phone.replace(/[\s\-().]/g, "")))
    errors.phone = "For WhatsApp, add your number with its country code, like +91 98765 43210.";
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
  const [values, setValues] = useState<FormFields>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [whatsappOptIn, setWhatsappOptIn] = useState(false);
  const { lines } = useLineList();

  const askQuantity = bool(settings, "showQuantity", true);
  const askGstin = bool(settings, "showGstin", true);
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const consent = optional(settings, "consentText");
  // The same merchant-typed setting `delivery-check` reads (`optional(settings, "storeSlug")`):
  // the request names the store in its body (public paths carry no variables), and the section
  // has no other way to know which store it is on.
  const slug = optional(settings, "storeSlug");

  function set<K extends keyof FormFields>(key: K, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Guard the second click: a submit already in flight ignores the next one.
    if (submitting) return;
    if (slug === null) {
      setFailure("This block has no store handle set, so nothing can be sent yet.");
      return;
    }
    const found = validate(values, askGstin, whatsappOptIn);
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    try {
      await submitEnquiry({
        ...values,
        whatsappOptIn,
        store: slug,
        lines: [...lines.values()].map((line) => ({
          product_id: line.productId,
          name: line.name,
          quantity: line.qty,
        })),
      });
      setDone(true);
      setValues(EMPTY);
      setWhatsappOptIn(false);
    } catch (error) {
      // The entered data stays exactly where it was — a failed send must never cost the buyer
      // the paragraph they just typed.
      setFailure(error instanceof Error ? error.message : "That didn't send. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function field(key: keyof FormFields, label: string, type: "text" | "email" | "tel" | "textarea") {
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
        {/* Marketing consent is opt-in and never pre-ticked; ticking it is recorded as the
            buyer's own WhatsApp opt-in on this store's consent ledger. */}
        <label
          htmlFor={`${base}-whatsapp`}
          style={{ display: "flex", gap: step(0.4), alignItems: "flex-start", fontFamily: T.fontBody, fontSize: "0.8125rem", color: T.text }}
        >
          <input
            id={`${base}-whatsapp`}
            type="checkbox"
            checked={whatsappOptIn}
            onChange={(event) => setWhatsappOptIn(event.target.checked)}
            style={{ marginTop: "0.2em" }}
          />
          <span>
            Send me offers and updates on WhatsApp (optional). Uses the phone number above; reply
            STOP at any time to stop.
          </span>
        </label>
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
