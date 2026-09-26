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
