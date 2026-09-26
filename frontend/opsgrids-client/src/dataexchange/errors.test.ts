import { describe, expect, it } from "vitest";

import { ApiError } from "../http";
import type { ProblemJson } from "../http";
import { NETWORK_MESSAGE, PREPARE_MESSAGE, isAbortError, prepareMessage, rowMessage } from "./errors";
import { RowProblem } from "./types";

describe("rowMessage", () => {
  it("gives a RowProblem's own sentence", () => {
    expect(rowMessage(new RowProblem("Company 'Acme' was not found"))).toBe("Company 'Acme' was not found");
  });

  it("gives the server's detail for an API error", () => {
    const error = new ApiError(409, "Request failed with 409", { detail: "A company named Acme already exists" });
    expect(rowMessage(error)).toBe("A company named Acme already exists");
  });

  it("falls back to the problem's title, then to the status line", () => {
    expect(rowMessage(new ApiError(403, "x", { title: "Forbidden" }))).toBe("Forbidden");
    expect(rowMessage(new ApiError(500, "Request failed with 500", null))).toBe("Request failed with 500");
  });

  it("appends the server's field errors as Field: message", () => {
    const error = new ApiError(422, "x", {
      detail: "Request validation failed",
      errors: [
        { field: "name", code: "string_too_short", message: "String should have at least 1 character" },
        { field: "address.city", code: "missing", message: "Field required" },
      ],
    });
    expect(rowMessage(error)).toBe(
      "Request validation failed (name: String should have at least 1 character; address.city: Field required)",
    );
  });

  it("says a sentence once when the server repeats it in the detail and in the one field error", () => {
    const sentence = "A supplier coded 'X' already exists";
    const error = new ApiError(409, "x", {
      detail: sentence,
      errors: [{ field: "code", code: "duplicate", message: sentence }],
    });
    expect(rowMessage(error)).toBe(sentence);
  });

  it("treats the repeat as the same sentence despite case, spacing and a closing full stop", () => {
    const error = new ApiError(409, "x", {
      detail: "A supplier coded 'X' already exists.",
      errors: [{ field: "code", code: "duplicate", message: "  a supplier coded 'x'   ALREADY EXISTS " }],
    });
    expect(rowMessage(error)).toBe("A supplier coded 'X' already exists.");
  });

  it("drops only the repeats: a field message that adds something stays, with its field", () => {
    const error = new ApiError(422, "x", {
      detail: "Validation failed",
      errors: [
        { field: "name", code: "missing", message: "Validation failed" },
        { field: "email", code: "value_error", message: "Not a valid email address" },
        { field: "phone", code: "value_error", message: "Not a valid email address" },
      ],
    });
    expect(rowMessage(error)).toBe(
      "Validation failed (email: Not a valid email address; phone: Not a valid email address)",
    );
  });

  it("gives just the headline when every field message repeats it", () => {
    const error = new ApiError(409, "x", {
      detail: "Already there",
      errors: [
        { field: "a", code: "duplicate", message: "already there" },
        { field: "b", code: "duplicate", message: "Already there." },
      ],
    });
    expect(rowMessage(error)).toBe("Already there");
  });

  it("drops a field error that is not the documented shape", () => {
    const error = new ApiError(422, "x", {
      detail: "Invalid",
      // What a misbehaving upstream could put on the wire, which the type does not allow.
      errors: [{ field: "name" }, "junk", null] as unknown as ProblemJson["errors"],
    });
    expect(rowMessage(error)).toBe("Invalid");
  });

  it("does not expose the machinery of a dropped connection or a parse failure", () => {
    expect(rowMessage(new TypeError("Failed to fetch"))).toBe(NETWORK_MESSAGE);
    expect(rowMessage(new SyntaxError("Unexpected token < in JSON"))).toBe(NETWORK_MESSAGE);
    expect(rowMessage("boom")).toBe(NETWORK_MESSAGE);
    expect(rowMessage(undefined)).toBe(NETWORK_MESSAGE);
  });
});

describe("prepareMessage", () => {
  it("keeps a RowProblem's sentence and hides anything else behind a generic one", () => {
    expect(prepareMessage(new RowProblem("Owner not found"))).toBe("Owner not found");
    expect(prepareMessage(new TypeError("x is undefined"))).toBe(PREPARE_MESSAGE);
  });
});

describe("isAbortError", () => {
  it("recognises the error an aborted fetch rejects with", () => {
    expect(isAbortError(new DOMException("Aborted", "AbortError"))).toBe(true);
    const named = new Error("The operation was aborted");
    named.name = "AbortError";
    expect(isAbortError(named)).toBe(true);
  });

  it("does not mistake anything else for it", () => {
    expect(isAbortError(new Error("nope"))).toBe(false);
    expect(isAbortError(new ApiError(500, "x", null))).toBe(false);
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError("AbortError")).toBe(false);
  });
});
