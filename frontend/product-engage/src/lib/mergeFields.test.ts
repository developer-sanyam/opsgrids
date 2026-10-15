import { describe, expect, it } from "vitest";

import { nonStandardFields, sampleFill } from "./mergeFields";

describe("merge fields", () => {
  it("flags non-standard names, ignoring case and spaces", () => {
    expect(nonStandardFields("Hi {{ First_Name }}", "{{compny_name|x}} {{Plan}}")).toEqual(["compny_name", "plan"]);
  });
  it("fills a sample, using the fallback for non-standard names", () => {
    expect(sampleFill("For {{ COMPANY_NAME }}, {{customer_name}} {{plan|Free}}")).toBe(
      "For Acme Traders, Priya Sharma Free",
    );
  });
});
