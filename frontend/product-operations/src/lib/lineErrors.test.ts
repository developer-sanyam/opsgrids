import { describe, expect, it } from "vitest";

import { lineFieldErrors } from "./lineErrors";

describe("lineFieldErrors", () => {
  it("reads both spellings of a line's field", () => {
    const errors = {
      "lines[1].from_location_id": "Location BIN-9 is not in the source warehouse",
      "lines.1.quantity": "must be > 0",
      "lines.0.quantity": "someone else's",
    };
    expect(lineFieldErrors(errors, "lines", 1)).toEqual({
      from_location_id: "Location BIN-9 is not in the source warehouse",
      quantity: "must be > 0",
    });
  });

  it("files a line-level failure under `line`", () => {
    expect(lineFieldErrors({ "lines[0]": "appears twice; combine the lines" }, "lines", 0)).toEqual({
      line: "appears twice; combine the lines",
    });
  });

  it("does not confuse line 1 with line 10", () => {
    expect(lineFieldErrors({ "lines.10.quantity": "x" }, "lines", 1)).toEqual({});
  });

  it("is empty when nothing belongs to the line", () => {
    expect(lineFieldErrors({ note: "too long" }, "lines", 0)).toEqual({});
  });
});
