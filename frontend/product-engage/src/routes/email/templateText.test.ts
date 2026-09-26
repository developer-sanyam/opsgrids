import { describe, expect, it } from "vitest";

import {
  insertAtSelection,
  isValidVariableName,
  placeholderToken,
  placeholdersIn,
  previewDocument,
} from "./templateText";

describe("placeholders", () => {
  it("finds each distinct placeholder once, in order, tolerating inner spaces", () => {
    expect(placeholdersIn("Hi {{ customer_name }}, order {{order.number}} and {{customer_name}}")).toEqual([
      "customer_name",
      "order.number",
    ]);
  });

  it("accepts the service's grammar and nothing else for a new variable name", () => {
    expect(isValidVariableName("customer_name")).toBe(true);
    expect(isValidVariableName("order.number")).toBe(true);
    expect(isValidVariableName("customer name")).toBe(false);
    expect(isValidVariableName("")).toBe(false);
    expect(placeholderToken("a")).toBe("{{a}}");
  });
});

describe("insertAtSelection", () => {
  it("writes the token at the caret and puts the caret after it", () => {
    expect(insertAtSelection("Hello , welcome", 6, 6, "{{name}}")).toEqual({
      value: "Hello {{name}}, welcome",
      caret: 14,
    });
  });

  it("replaces a selection", () => {
    expect(insertAtSelection("Hello NAME", 6, 10, "{{name}}").value).toBe("Hello {{name}}");
  });

  it("copes with a backwards selection", () => {
    expect(insertAtSelection("Hello NAME", 10, 6, "{{name}}").value).toBe("Hello {{name}}");
  });
});

describe("previewDocument", () => {
  it("escapes anything that could be markup, so a script in a body is text", () => {
    const html = previewDocument("<b>Hi</b>", '<script>alert("x")</script> & more');
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; more");
  });

  it("highlights placeholders and forbids everything but inline style by policy", () => {
    const html = previewDocument("Order {{order_number}}", "Hi {{ customer_name }}");
    expect(html).toContain("<mark>{{order_number}}</mark>");
    expect(html).toContain("<mark>{{customer_name}}</mark>");
    expect(html).toContain("default-src 'none'");
  });
});
