import { describe, expect, it } from "vitest";

import { csvCell } from "./csv";

describe("csvCell", () => {
  it("neutralises anything a spreadsheet would run as a formula", () => {
    expect(csvCell('=HYPERLINK("https://evil")')).toBe(`"'=HYPERLINK(""https://evil"")"`);
    expect(csvCell("+1+1")).toBe("'+1+1");
    expect(csvCell("-2+3")).toBe("'-2+3");
    expect(csvCell("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(csvCell("\t=1")).toBe("'\t=1");
    expect(csvCell("\r=1")).toBe(`"'\r=1"`);
  });

  it("leaves plain values, including negative numbers, untouched", () => {
    expect(csvCell("Acme")).toBe("Acme");
    expect(csvCell("-12.50")).toBe("-12.50");
    expect(csvCell("a@b.com")).toBe("a@b.com");
  });

  it("treats only well-formed numbers as safe", () => {
    expect(csvCell("-1,200")).toBe(`"-1,200"`);
    expect(csvCell("-1,20,000.50")).toBe(`"-1,20,000.50"`);
    expect(csvCell("+SUM()")).toBe("'+SUM()");
    expect(csvCell("-,")).toBe(`"'-,"`);
    expect(csvCell("-.")).toBe("'-.");
    expect(csvCell("-1.2.3")).toBe("'-1.2.3");
  });

  it("still quotes per RFC 4180", () => {
    expect(csvCell('say "hi", ok')).toBe(`"say ""hi"", ok"`);
    expect(csvCell("line\nbreak")).toBe(`"line\nbreak"`);
  });
});
