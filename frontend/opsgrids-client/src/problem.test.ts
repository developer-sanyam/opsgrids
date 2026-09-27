/**
 * The field-error shape is a contract with `platform_core.errors`, so the fixtures here are the
 * bodies that module actually produces — a pydantic 422 with its `body.` prefix already stripped,
 * and a hand-raised one from a service. If either of these stops parsing, forms stop pointing at
 * the field that failed and start saying "something went wrong".
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "./http";
import { fieldError, fieldErrorMap, fieldErrors, problemMessage, problemSlug } from "./problem";

/** `register_error_handlers`' `RequestValidationError` branch, verbatim. */
const VALIDATION = {
  type: "https://errors.opsgrids.dev/validation",
  title: "Validation failed",
  status: 422,
  detail: "Request payload failed validation",
  instance: "/api/v1/widgets",
  request_id: "7f3c1a2e",
  errors: [
    { field: "name", code: "string_too_short", message: "String should have at least 2 characters" },
    { field: "parts.0.sku", code: "missing", message: "Field required" },
  ],
};

describe("fieldErrors", () => {
  it("reads the platform's errors[] off a validation problem", () => {
    expect(fieldErrors(VALIDATION)).toHaveLength(2);
    expect(fieldError(VALIDATION, "name")).toBe("String should have at least 2 characters");
  });

  it("keeps the nested dotted path the server sends, without the body prefix", () => {
    expect(fieldError(VALIDATION, "parts.0.sku")).toBe("Field required");
    expect(fieldError(VALIDATION, "body.parts.0.sku")).toBeUndefined();
  });

  it("is empty for a problem with no field detail, and for no problem at all", () => {
    expect(fieldErrors({ status: 403, detail: "Not permitted" })).toEqual([]);
    expect(fieldErrors(null)).toEqual([]);
    expect(fieldErrors(undefined)).toEqual([]);
  });

  it("drops an entry that is not the documented shape rather than rendering half of it", () => {
    const problem = {
      status: 422,
      errors: [{ field: "name" }, "broken", { field: "sku", code: "taken", message: "Taken" }],
    } as unknown as { errors: { field: string; code: string; message: string }[] };
    expect(fieldErrors(problem).map((e) => e.field)).toEqual(["sku"]);
  });

  it("keeps a service's extra keys on the entry without requiring them", () => {
    // `services/account` attaches `employee_id` alongside the three guaranteed keys.
    const problem = {
      status: 409,
      errors: [
        {
          field: "employee_id",
          code: "employee_already_linked",
          message: "That employee record is already linked to another member.",
          employee_id: "0191f3",
        },
      ],
    };
    expect(fieldError(problem, "employee_id")).toBe(
      "That employee record is already linked to another member.",
    );
  });
});

describe("fieldErrorMap", () => {
  it("keys messages by field for spreading across a form", () => {
    expect(fieldErrorMap(VALIDATION)).toEqual({
      name: "String should have at least 2 characters",
      "parts.0.sku": "Field required",
    });
  });

  it("keeps the first message when one field failed twice", () => {
    const problem = {
      errors: [
        { field: "name", code: "missing", message: "Field required" },
        { field: "name", code: "string_too_short", message: "Too short" },
      ],
    };
    expect(fieldErrorMap(problem).name).toBe("Field required");
  });
});

describe("problemSlug", () => {
  it("is the last segment of the type URI, lower-cased", () => {
    expect(problemSlug({ type: "https://errors.opsgrids.dev/password-locked" })).toBe("password-locked");
    expect(problemSlug({ type: "https://errors.opsgrids.dev/MFA-Locked/" })).toBe("mfa-locked");
    expect(problemSlug({ type: "mfa-not-enrolled" })).toBe("mfa-not-enrolled");
  });

  it("is empty when there is no type, or no problem at all", () => {
    expect(problemSlug({ status: 403 })).toBe("");
    expect(problemSlug({ type: "" })).toBe("");
    expect(problemSlug(null)).toBe("");
    expect(problemSlug(undefined)).toBe("");
  });
});

describe("problemMessage", () => {
  it("uses the gateway's own detail, which is already a sentence", () => {
    const error = new ApiError(409, "fallback", {
      detail: "That conflicts with something that already exists",
    });
    expect(problemMessage(error)).toBe("That conflicts with something that already exists");
  });

  it("falls back to the title when a problem carries no detail", () => {
    expect(problemMessage(new ApiError(503, "x", { title: "Service unavailable" }))).toBe(
      "Service unavailable",
    );
  });

  it("does not put machinery in front of a user for a dropped connection", () => {
    expect(problemMessage(new TypeError("Failed to fetch"))).toBe(
      "Something went wrong. Try again.",
    );
  });
});
