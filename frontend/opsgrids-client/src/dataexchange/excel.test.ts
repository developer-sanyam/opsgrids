import { describe, expect, it } from "vitest";

import { MAX_CELL_CHARACTERS, clipCell } from "./excel";

function hasLoneSurrogate(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(i + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      i += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

describe("clipCell: characters XML cannot carry", () => {
  it("removes control characters and DEL but keeps a tab, a line feed and a carriage return", () => {
    expect(clipCell("a\u0000b\u0001c\u0008d\u000be\u000cf\u001fg\u007fh")).toBe("abcdefgh");
    expect(clipCell("a\tb\nc\rd")).toBe("a\tb\nc\rd");
  });

  it("removes U+FFFE and U+FFFF", () => {
    expect(clipCell("a￾b￿c")).toBe("abc");
  });

  it("removes the non-characters U+FDD0 to U+FDEF, and only those", () => {
    expect(clipCell("a﷐b﷕c﷯d")).toBe("abcd");
    expect(clipCell("a﷏bﷰc")).toBe("a﷏bﷰc");
  });

  it("removes a surrogate that has lost its partner, from either side", () => {
    expect(clipCell("a\uD800b")).toBe("ab");
    expect(clipCell("a\uDC00b")).toBe("ab");
    expect(clipCell("a\uD83D")).toBe("a");
    expect(clipCell("\uDE00a\uD83D")).toBe("a");
    expect(clipCell("\uD800𐀀")).toBe("𐀀");
  });

  it("keeps a pair, and everything else Unicode has", () => {
    expect(clipCell("smile 😀 and 𝄞")).toBe("smile 😀 and 𝄞");
    expect(clipCell("zażółć gęślą jaźń — 日本語 — العربية")).toBe("zażółć gęślą jaźń — 日本語 — العربية");
  });

  it("leaves ordinary text untouched", () => {
    expect(clipCell("")).toBe("");
    expect(clipCell("=HYPERLINK(\"x\") +1 @SUM")).toBe("=HYPERLINK(\"x\") +1 @SUM");
  });
});

describe("clipCell: Excel's length limit", () => {
  it("keeps text at the limit and cuts text over it, marking the cut", () => {
    expect(clipCell("x".repeat(MAX_CELL_CHARACTERS))).toHaveLength(MAX_CELL_CHARACTERS);
    const cut = clipCell("x".repeat(MAX_CELL_CHARACTERS + 1));
    expect(cut).toHaveLength(MAX_CELL_CHARACTERS);
    expect(cut.endsWith("…")).toBe(true);
  });

  it("does not cut a surrogate pair in half", () => {
    const text = `${"x".repeat(MAX_CELL_CHARACTERS - 2)}😀tail`;
    const cut = clipCell(text);
    expect(cut.length).toBeLessThanOrEqual(MAX_CELL_CHARACTERS);
    expect(hasLoneSurrogate(cut)).toBe(false);
    expect(cut.endsWith("…")).toBe(true);
  });

  it("counts the length after removing what cannot be written", () => {
    const text = `${"x".repeat(MAX_CELL_CHARACTERS)}\u0000\u0000￾`;
    expect(clipCell(text)).toBe("x".repeat(MAX_CELL_CHARACTERS));
  });
});
