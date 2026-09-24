/**
 * The avatar is decoration beside a name that is always present, so what is worth pinning is that
 * it stays out of the accessibility tree, is stable per name, and that the two shapes — a circle
 * for a person, a rounded square for an organization — are not confused.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Avatar } from "./Avatar";

function spanFor(name: string, props: { shape?: "circle" | "square" } = {}) {
  const { container } = render(<Avatar name={name} {...props} />);
  const span = container.querySelector("span");
  if (span === null) throw new Error("no avatar rendered");
  return span;
}

describe("Avatar", () => {
  it("renders initials and hides itself from assistive technology", () => {
    const span = spanFor("Northwind Metals");
    expect(span.textContent).toBe("NM");
    expect(span.getAttribute("aria-hidden")).toBe("true");
  });

  it("is a circle by default", () => {
    const span = spanFor("Asha Rao");
    expect(span.className).toContain("rounded-full");
    expect(span.className).not.toContain("rounded-tile");
  });

  it("is a rounded square for an organization", () => {
    const span = spanFor("Acme Ltd", { shape: "square" });
    expect(span.className).toContain("rounded-tile");
    expect(span.className).not.toContain("rounded-full");
  });

  it("picks the same tint for the same name every time", () => {
    const first = spanFor("Acme Ltd").className;
    const second = spanFor("Acme Ltd").className;
    expect(first).toBe(second);
  });
});
