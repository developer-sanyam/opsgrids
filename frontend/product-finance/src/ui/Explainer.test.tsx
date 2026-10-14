import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Explainer } from "./Explainer";

describe("Explainer", () => {
  it("is one line with a Learn more control until it is opened", () => {
    render(<Explainer>Accepting a document claims the credit on it.</Explainer>);
    const toggle = screen.getByRole("button", { name: "Learn more" });
    const text = screen.getByText("Accepting a document claims the credit on it.");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(text.className).toContain("line-clamp-1");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Show less" }).getAttribute("aria-expanded")).toBe("true");
    expect(text.className).not.toContain("line-clamp-1");
  });
});
