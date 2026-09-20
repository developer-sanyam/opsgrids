/**
 * The authored-prose convention, as a pure unit.
 *
 * This is the part of the CMS pack worth testing exhaustively: it is the one place where a
 * merchant's free text becomes document structure, it runs on fifteen pages, and every one of its
 * failure modes is silent — a mis-parsed list is a paragraph full of hyphens, which renders
 * perfectly happily and is wrong.
 */

import { describe, expect, it } from "vitest";

import { longFormHeadings, parseLongForm, slugify } from "./prose";

describe("parsing authored prose", () => {
  it("returns nothing at all for an empty field", () => {
    expect(parseLongForm("")).toEqual([]);
    expect(parseLongForm("   \n\n  \n")).toEqual([]);
  });

  it("makes a subheading out of a leading ##, with an anchor", () => {
    expect(parseLongForm("## What we collect")).toEqual([
      { kind: "heading", text: "What we collect", slug: "what-we-collect" },
    ]);
  });

  it("leaves a stray # alone — this is not Markdown", () => {
    const blocks = parseLongForm("# Not a heading\n\n#hashtag\n\n### Also not one");
    expect(blocks).toEqual([
      { kind: "paragraph", text: "# Not a heading" },
      { kind: "paragraph", text: "#hashtag" },
      { kind: "paragraph", text: "### Also not one" },
    ]);
  });

  it("separates paragraphs on a blank line and reflows the lines inside one", () => {
    const blocks = parseLongForm("First sentence.\nStill the first paragraph.\n\nSecond paragraph.");
    expect(blocks).toEqual([
      { kind: "paragraph", text: "First sentence. Still the first paragraph." },
      { kind: "paragraph", text: "Second paragraph." },
    ]);
  });

  it("collects consecutive bullets into one list", () => {
    const blocks = parseLongForm("- Your name\n- Your email address\n* Your delivery address");
    expect(blocks).toEqual([
      {
        kind: "list",
        ordered: false,
        items: ["Your name", "Your email address", "Your delivery address"],
      },
    ]);
  });

  it("handles a field that is only a list, with no prose around it", () => {
    const blocks = parseLongForm("- One\n- Two");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toEqual({ kind: "list", ordered: false, items: ["One", "Two"] });
  });

  it("makes a numbered list out of clause numbering, which is what a Terms page is", () => {
    const blocks = parseLongForm("1. Definitions\n2) Scope of these terms");
    expect(blocks).toEqual([
      { kind: "list", ordered: true, items: ["Definitions", "Scope of these terms"] },
    ]);
  });

  it("does not mistake a year or a price for clause numbering", () => {
    expect(parseLongForm("1994 was the first year we shipped.")).toEqual([
      { kind: "paragraph", text: "1994 was the first year we shipped." },
    ]);
  });

  it("splits a bulleted run from a numbered run instead of merging the markers", () => {
    const blocks = parseLongForm("- Alpha\n1. Beta");
    expect(blocks).toEqual([
      { kind: "list", ordered: false, items: ["Alpha"] },
      { kind: "list", ordered: true, items: ["Beta"] },
    ]);
  });

  it("ends an open list or paragraph at the next subheading, with no blank line needed", () => {
    const blocks = parseLongForm("Intro line.\n## Cookies\n- A session cookie\n## Contact");
    expect(blocks.map((block) => block.kind)).toEqual([
      "paragraph",
      "heading",
      "list",
      "heading",
    ]);
  });

  it("ignores a lone ## with no words after it — a merchant mid-edit, not a heading", () => {
    expect(parseLongForm("##\n##   ")).toEqual([]);
  });

  it("parses the shape a real policy page is written in", () => {
    const blocks = parseLongForm(
      [
        "We keep this policy short.",
        "",
        "## What we collect",
        "",
        "Only what an order needs:",
        "",
        "- Your name and phone number",
        "- The delivery address",
        "",
        "## How long we keep it",
        "",
        "Eight years, which is what the tax rules require.",
      ].join("\n"),
    );

    expect(blocks.map((block) => block.kind)).toEqual([
      "paragraph",
      "heading",
      "paragraph",
      "list",
      "heading",
      "paragraph",
    ]);
  });
});

describe("anchors", () => {
  it("slugs a heading into something that can be linked to from an email", () => {
    expect(slugify("Returns & Refunds")).toBe("returns-refunds");
    expect(slugify("  7. Limitation of liability  ")).toBe("7-limitation-of-liability");
  });

  it("keeps letters that are not ASCII rather than collapsing the whole heading", () => {
    expect(slugify("Garantía")).toBe("garantía");
    expect(slugify("!!!")).toBe("section");
  });

  it("suffixes a repeated heading so two anchors in one section cannot collide", () => {
    const blocks = parseLongForm("## Charges\n\nOne.\n\n## Charges\n\nTwo.\n\n## Charges");
    expect(longFormHeadings(blocks).map((heading) => heading.slug)).toEqual([
      "charges",
      "charges-2",
      "charges-3",
    ]);
  });

  it("gives the table of contents exactly the section's headings, in order", () => {
    const blocks = parseLongForm("## One\n\ntext\n\n- item\n\n## Two\n\n## Three");
    expect(longFormHeadings(blocks)).toEqual([
      { text: "One", slug: "one" },
      { text: "Two", slug: "two" },
      { text: "Three", slug: "three" },
    ]);
  });
});
