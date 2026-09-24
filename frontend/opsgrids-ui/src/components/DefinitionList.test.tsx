/**
 * A record's summary is read as terms and values, so the markup has to say which is which: a real
 * `<dl>`, with each `<dt>` beside the `<dd>` it names, and the grid's columns left to the caller.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { DefinitionList } from "./DefinitionList";

describe("DefinitionList", () => {
  it("renders every pair as a term and its value", () => {
    const { container } = render(
      <DefinitionList
        items={[
          { term: "Seats", value: "12" },
          { term: "Customer since", value: "14 Feb 2026" },
        ]}
      />,
    );
    const list = container.querySelector("dl");
    expect(list).not.toBeNull();
    expect(list?.querySelectorAll("dt")).toHaveLength(2);
    expect(list?.querySelectorAll("dd")).toHaveLength(2);
    expect(screen.getByText("Seats").tagName).toBe("DT");
    expect(screen.getByText("14 Feb 2026").tagName).toBe("DD");
  });

  it("keeps each term beside its own value, so a wrapping grid cannot separate them", () => {
    const { container } = render(
      <DefinitionList items={[{ term: "MRR", value: "₹45,000" }]} />,
    );
    const group = container.querySelector("dl > div");
    expect(group).not.toBeNull();
    if (group === null) return;
    expect(within(group as HTMLElement).getByText("MRR")).toBeDefined();
    expect(within(group as HTMLElement).getByText("₹45,000")).toBeDefined();
  });

  it("takes a node as a value, for a link or a formatted amount", () => {
    render(
      <DefinitionList
        items={[{ term: "Primary contact", value: <a href="mailto:a@b.example">Asha</a> }]}
      />,
    );
    expect(screen.getByRole("link", { name: "Asha" }).getAttribute("href")).toBe(
      "mailto:a@b.example",
    );
  });

  it("leaves the column count to the caller", () => {
    const { container } = render(
      <DefinitionList items={[{ term: "A", value: "1" }]} className="sm:grid-cols-3" />,
    );
    const list = container.querySelector("dl");
    expect(list?.className).toContain("sm:grid-cols-3");
  });

  it("renders nothing to read for an empty list, and does not throw", () => {
    const { container } = render(<DefinitionList items={[]} />);
    expect(container.querySelectorAll("dt")).toHaveLength(0);
  });
});
