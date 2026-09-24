/**
 * The settings form's own validation, values and mapping to `StorePut` — kept out of the
 * component so the rules are testable with no DOM, the way `delivery/zoneForm.ts` does it.
 */

import type { StoreOut, StorePut } from "./types";

export interface StoreFormValues {
  slug: string;
  name: string;
  tagline: string;
  logoText: string;
  currency: string;
  timezone: string;
}

export const EMPTY_STORE_FORM: StoreFormValues = {
  slug: "",
  name: "",
  tagline: "",
  logoText: "",
  currency: "INR",
  timezone: "Asia/Kolkata",
};

export function fromStore(store: StoreOut): StoreFormValues {
  return {
    slug: store.slug,
    name: store.name,
    tagline: store.tagline ?? "",
    logoText: store.logo_text ?? "",
    currency: store.currency,
    timezone: store.timezone,
  };
}

export type StoreFormErrors = Partial<Record<keyof StoreFormValues, string>>;

const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,62}$/;
const CURRENCY_PATTERN = /^[A-Z]{3}$/;

/** Mirrors the server's rules (`StorePut`, `routes.py:106-121`) and never replaces them. */
export function validateStoreForm(values: StoreFormValues): StoreFormErrors {
  const errors: StoreFormErrors = {};
  if (!SLUG_PATTERN.test(values.slug)) {
    errors.slug =
      "Lowercase letters, digits and hyphens, starting with a letter or digit, 2-63 characters.";
  }
  if (values.name.trim() === "") errors.name = "Give the store a name.";
  else if (values.name.trim().length > 80) errors.name = "80 characters or fewer.";
  if (values.tagline.length > 160) errors.tagline = "160 characters or fewer.";
  if (values.logoText.length > 12) errors.logoText = "12 characters or fewer.";
  if (!CURRENCY_PATTERN.test(values.currency)) errors.currency = "A 3-letter ISO code, like INR.";
  if (values.timezone.trim() === "") errors.timezone = "Pick a timezone.";
  return errors;
}

/** Blank clears a nullable field — the server's own rule for `tagline` and `logo_text`
 *  (`StorePut`'s field descriptions). The domain is never sent — `StorePut` no longer has a
 *  `custom_domain` field at all; the domain screen (`settings/domain.ts`) owns it. */
export function toStorePut(values: StoreFormValues): StorePut {
  const tagline = values.tagline.trim();
  const logoText = values.logoText.trim();
  return {
    slug: values.slug.trim(),
    name: values.name.trim(),
    tagline: tagline === "" ? null : tagline,
    logo_text: logoText === "" ? null : logoText,
    currency: values.currency.trim().toUpperCase(),
    timezone: values.timezone.trim(),
  };
}
