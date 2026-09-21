import { describe, expect, it } from "vitest";

import { EMPTY_STORE_FORM, fromStore, toStorePut, validateStoreForm } from "./storeForm";
import type { StoreOut } from "./types";

const STORE: StoreOut = {
  id: "store-1",
  slug: "greenleaf",
  name: "Greenleaf Trading Co.",
  tagline: "Reliable stock, quoted fast",
  logo_text: "GTC",
  status: "draft",
  currency: "INR",
  timezone: "Asia/Kolkata",
  custom_domain: null,
  custom_domain_status: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

describe("validateStoreForm", () => {
  it("refuses a slug that doesn't match the server's pattern", () => {
    const errors = validateStoreForm({ ...EMPTY_STORE_FORM, slug: "A", name: "Greenleaf" });
    expect(errors.slug).toBeDefined();
  });

  it("accepts a valid slug, name, currency and timezone with nothing else filled in", () => {
    const errors = validateStoreForm({ ...EMPTY_STORE_FORM, slug: "greenleaf", name: "Greenleaf" });
    expect(errors).toEqual({});
  });

  it("requires a name", () => {
    const errors = validateStoreForm({ ...EMPTY_STORE_FORM, slug: "greenleaf", name: "" });
    expect(errors.name).toBe("Give the store a name.");
  });

  it("refuses a currency that isn't a 3-letter code", () => {
    const errors = validateStoreForm({
      ...EMPTY_STORE_FORM,
      slug: "greenleaf",
      name: "Greenleaf",
      currency: "Rupees",
    });
    expect(errors.currency).toBeDefined();
  });
});

describe("toStorePut", () => {
  it("blanks a cleared tagline, logo text and custom domain to null — never an empty string", () => {
    const put = toStorePut({
      ...EMPTY_STORE_FORM,
      slug: "greenleaf",
      name: "Greenleaf",
      tagline: "  ",
      logoText: "",
      customDomain: " ",
    });
    expect(put.tagline).toBeNull();
    expect(put.logo_text).toBeNull();
    expect(put.custom_domain).toBeNull();
  });

  it("upper-cases the currency and lower-cases the domain, trimming both", () => {
    const put = toStorePut({
      ...EMPTY_STORE_FORM,
      slug: "greenleaf",
      name: "Greenleaf",
      currency: " inr ",
      customDomain: " Shop.Example.COM ",
    });
    expect(put.currency).toBe("INR");
    expect(put.custom_domain).toBe("shop.example.com");
  });
});

describe("fromStore", () => {
  it("turns a null tagline, logo text and domain into empty strings for the inputs", () => {
    const values = fromStore({ ...STORE, tagline: null, logo_text: null, custom_domain: null });
    expect(values.tagline).toBe("");
    expect(values.logoText).toBe("");
    expect(values.customDomain).toBe("");
  });

  it("round-trips a fully populated store", () => {
    const values = fromStore(STORE);
    expect(values).toEqual({
      slug: "greenleaf",
      name: "Greenleaf Trading Co.",
      tagline: "Reliable stock, quoted fast",
      logoText: "GTC",
      currency: "INR",
      timezone: "Asia/Kolkata",
      customDomain: "",
    });
  });
});
