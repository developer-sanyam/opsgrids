import { describe, expect, it } from "vitest";

import {
  alignExamples,
  EMPTY_DRAFT,
  exampleValues,
  insertVariable,
  LIMITS,
  nextVariableIndex,
  stepOfField,
  substitute,
  toInput,
  validateContent,
  validateDetails,
  validateDraft,
  variableIndexes,
  wrapSelection,
} from "./draft";
import type { Draft } from "./draft";

const VALID: Draft = {
  ...EMPTY_DRAFT,
  name: "order_shipped",
  body: "Hello {{1}}, your order {{2}} has shipped.",
  examples: ["Rahul", "#ORD-2938"],
};

function with_(patch: Partial<Draft>): Draft {
  return { ...VALID, ...patch };
}

describe("name", () => {
  it.each(["order_shipped", "a", "otp_2fa", "x_1_y"])("accepts %s", (name) => {
    expect(validateDetails(with_({ name }))["name"]).toBeUndefined();
  });

  it.each(["Order_Shipped", "order shipped", "order-shipped", "order.shipped", "délivré"])(
    "refuses %s",
    (name) => {
      expect(validateDetails(with_({ name }))["name"]).toMatch(/lowercase letters, numbers and underscores/i);
    },
  );

  it("requires a name and caps its length", () => {
    expect(validateDetails(with_({ name: "  " }))["name"]).toBe("Enter a template name.");
    expect(validateDetails(with_({ name: "a".repeat(LIMITS.name + 1) }))["name"]).toMatch(/512/);
  });

  it("requires a language", () => {
    expect(validateDetails(with_({ language: "" }))["language"]).toBe("Choose a language.");
  });
});

describe("variables", () => {
  it("reads the distinct numbers in a body, in order", () => {
    expect(variableIndexes("{{2}} then {{1}} then {{2}} again")).toEqual([1, 2]);
    expect(variableIndexes("nothing here")).toEqual([]);
  });

  it("accepts contiguous numbering starting at 1, and repeats", () => {
    expect(validateContent(with_({ body: "{{1}} and {{2}} and {{1}}" }))["components.body.text"]).toBeUndefined();
  });

  it("refuses a gap, and says what the numbering should be", () => {
    const error = validateContent(with_({ body: "Hi {{1}} and {{3}}", examples: ["a", "b"] }))["components.body.text"];
    expect(error).toMatch(/no gaps/);
    expect(error).toContain("{{1}}, {{3}}");
    expect(error).toContain("{{1}}, {{2}}");
  });

  it("refuses numbering that does not start at 1", () => {
    expect(validateContent(with_({ body: "Hi {{2}}", examples: ["a"] }))["components.body.text"]).toMatch(/starting at \{\{1\}\}/);
  });

  it("refuses named or spaced variables", () => {
    expect(validateContent(with_({ body: "Hi {{name}}" }))["components.body.text"]).toMatch(/numbered variables/);
    expect(validateContent(with_({ body: "Hi {{ 1 }}" }))["components.body.text"]).toMatch(/numbered variables/);
  });

  it("numbers the next variable one past the highest", () => {
    expect(nextVariableIndex("")).toBe(1);
    expect(nextVariableIndex("Hi {{1}} {{2}}")).toBe(3);
  });
});

describe("examples", () => {
  it("wants one example per variable and names the ones missing", () => {
    const errors = validateContent(with_({ examples: ["Rahul", ""] }));
    expect(errors["components.body.examples.1"]).toBe("Enter an example for {{2}}.");
    expect(errors["components.body.examples.0"]).toBeUndefined();
    expect(errors["components.body.examples"]).toMatch(/one example for each of the 2 variables/);
  });

  it("wants none when the body has no variables", () => {
    expect(validateContent(with_({ body: "Thanks for shopping.", examples: [] }))).toEqual({});
  });

  it("does not repeat a numbering mistake as an examples mistake", () => {
    const errors = validateContent(with_({ body: "Hi {{1}} and {{3}}", examples: [] }));
    expect(Object.keys(errors)).toEqual(["components.body.text"]);
  });

  it("aligns typed examples to the variable count, keeping what was typed", () => {
    expect(alignExamples(["a", "b", "c"], 2)).toEqual(["a", "b"]);
    expect(alignExamples(["a"], 3)).toEqual(["a", "", ""]);
  });
});

describe("length limits", () => {
  it("caps the body at 1,024 characters", () => {
    const ok = validateContent(with_({ body: "a".repeat(LIMITS.body), examples: [] }));
    const over = validateContent(with_({ body: "a".repeat(LIMITS.body + 1), examples: [] }));
    expect(ok["components.body.text"]).toBeUndefined();
    expect(over["components.body.text"]).toMatch(/1,024/);
  });

  it("requires body text", () => {
    expect(validateContent(with_({ body: " ", examples: [] }))["components.body.text"]).toBe("Enter the message text.");
  });

  it("caps the footer at 60 characters and refuses variables in it", () => {
    expect(validateContent(with_({ footer: "f".repeat(61) }))["components.footer.text"]).toMatch(/60/);
    expect(validateContent(with_({ footer: "Ref {{1}}" }))["components.footer.text"]).toMatch(/cannot contain variables/);
    expect(validateContent(with_({ footer: "f".repeat(60) }))["components.footer.text"]).toBeUndefined();
  });
});

describe("header", () => {
  it("needs text for a text header and caps it at 60", () => {
    expect(validateContent(with_({ headerType: "text", headerText: "" }))["components.header.text"]).toBe("Enter the header text.");
    expect(validateContent(with_({ headerType: "text", headerText: "h".repeat(61) }))["components.header.text"]).toMatch(/60/);
  });

  it("allows one variable, {{1}}, and then wants its example", () => {
    const errors = validateContent(with_({ headerType: "text", headerText: "Order {{1}}", headerExample: "" }));
    expect(errors["components.header.example"]).toMatch(/example value for \{\{1\}\}/);
    expect(validateContent(with_({ headerType: "text", headerText: "Order {{1}} {{2}}" }))["components.header.text"]).toMatch(/one variable/);
    expect(validateContent(with_({ headerType: "text", headerText: "Order {{1}}", headerExample: "#1" }))).toEqual({});
  });

  it("needs a sample link for a media header", () => {
    expect(validateContent(with_({ headerType: "image", headerExample: "" }))["components.header.example"]).toMatch(/sample link/);
    expect(validateContent(with_({ headerType: "image", headerExample: "https://x.test/a.jpg" }))).toEqual({});
  });
});

describe("buttons", () => {
  const quick = { type: "quick_reply", text: "Yes", url: "", phone: "" } as const;
  const link = { type: "url", text: "Track", url: "https://example.com/t", phone: "" } as const;
  const call = { type: "phone", text: "Call us", url: "", phone: "+919876543210" } as const;

  it("accepts a valid mix", () => {
    expect(validateContent(with_({ buttons: [quick, link, call] }))).toEqual({});
  });

  it("needs a label of at most 25 characters", () => {
    const errors = validateContent(with_({ buttons: [{ ...quick, text: "" }, { ...quick, text: "x".repeat(26) }] }));
    expect(errors["components.buttons.0.text"]).toBe("Enter the button label.");
    expect(errors["components.buttons.1.text"]).toMatch(/25/);
  });

  it("needs a full link for a link button", () => {
    expect(validateContent(with_({ buttons: [{ ...link, url: "" }] }))["components.buttons.0.url"]).toMatch(/Enter the link/);
    expect(validateContent(with_({ buttons: [{ ...link, url: "example.com" }] }))["components.buttons.0.url"]).toMatch(/starting with https/);
  });

  it("needs an international number for a call button", () => {
    expect(validateContent(with_({ buttons: [{ ...call, phone: "98765" }] }))["components.buttons.0.phone"]).toMatch(/country code/);
    expect(validateContent(with_({ buttons: [{ ...call, phone: "09876543210" }] }))["components.buttons.0.phone"]).toBeDefined();
  });

  it("allows two link buttons, one call button and ten in all", () => {
    expect(validateContent(with_({ buttons: [link, link, link] }))["components.buttons"]).toMatch(/2 link buttons/);
    expect(validateContent(with_({ buttons: [call, call] }))["components.buttons"]).toMatch(/one call button/);
    expect(validateContent(with_({ buttons: Array.from({ length: 11 }, () => quick) }))["components.buttons"]).toMatch(/10 buttons/);
    expect(validateContent(with_({ buttons: Array.from({ length: 10 }, () => quick) }))["components.buttons"]).toBeUndefined();
  });
});

describe("substitution", () => {
  it("fills numbered placeholders and leaves the unfilled ones as written", () => {
    expect(substitute("Hi {{1}}, order {{2}}", { "1": "Rahul" })).toBe("Hi Rahul, order {{2}}");
    expect(substitute("Hi {{1}}", { "1": "  " })).toBe("Hi {{1}}");
    expect(substitute("{{1}}-{{1}}", { "1": "x" })).toBe("x-x");
  });

  it("builds the values map from the examples in order", () => {
    expect(exampleValues(["a", "b"])).toEqual({ "1": "a", "2": "b" });
  });
});

describe("request body", () => {
  it("sends only the parts that were filled in", () => {
    expect(toInput(VALID)).toEqual({
      name: "order_shipped",
      category: "utility",
      language: "en_US",
      components: { body: { text: VALID.body, examples: ["Rahul", "#ORD-2938"] } },
    });
  });

  it("carries header, footer and buttons when present, trimmed to their wire shape", () => {
    const input = toInput(
      with_({
        headerType: "text",
        headerText: "Order {{1}}",
        headerExample: " #1 ",
        footer: " Thanks ",
        buttons: [
          { type: "quick_reply", text: " Yes ", url: "ignored", phone: "ignored" },
          { type: "url", text: "Track", url: " https://example.com ", phone: "" },
          { type: "phone", text: "Call", url: "", phone: "+919876543210" },
        ],
      }),
    );
    expect(input.components.header).toEqual({ type: "text", text: "Order {{1}}", example: "#1" });
    expect(input.components.footer).toEqual({ text: "Thanks" });
    expect(input.components.buttons).toEqual([
      { type: "quick_reply", text: "Yes" },
      { type: "url", text: "Track", url: "https://example.com" },
      { type: "phone", text: "Call", phone: "+919876543210" },
    ]);
  });

  it("sends the media sample as the header example, and no example for a plain text header", () => {
    expect(toInput(with_({ headerType: "video", headerExample: "https://x.test/v.mp4" })).components.header).toEqual({
      type: "video",
      example: "https://x.test/v.mp4",
    });
    expect(toInput(with_({ headerType: "text", headerText: "Your order" })).components.header).toEqual({
      type: "text",
      text: "Your order",
    });
  });

  it("drops examples beyond the body's variables", () => {
    expect(toInput(with_({ body: "Hi {{1}}", examples: ["a", "stale"] })).components.body.examples).toEqual(["a"]);
  });

  it("passes a whole valid draft", () => {
    expect(validateDraft(VALID)).toEqual({});
  });
});

describe("field to step", () => {
  it("sends details fields to step 0 and components to step 1", () => {
    expect(stepOfField("name")).toBe(0);
    expect(stepOfField("language")).toBe(0);
    expect(stepOfField("components.body.examples.0")).toBe(1);
    expect(stepOfField("components")).toBe(1);
    expect(stepOfField("something_else")).toBeNull();
  });
});

describe("editing helpers", () => {
  it("wraps a selection in formatting marks and keeps it selected", () => {
    expect(wrapSelection("say hello now", 4, 9, "*")).toEqual({ value: "say *hello* now", selection: [5, 10] });
  });

  it("puts marks around an empty caret", () => {
    expect(wrapSelection("ab", 1, 1, "_")).toEqual({ value: "a__b", selection: [2, 2] });
  });

  it("inserts the next variable at the caret", () => {
    expect(insertVariable("Hi {{1}}, ", 10, 10)).toEqual({ value: "Hi {{1}}, {{2}}", selection: [15, 15] });
    expect(insertVariable("", 0, 0).value).toBe("{{1}}");
  });
});
