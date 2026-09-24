/**
 * The door every unauthenticated screen shares. What is worth pinning is the structure a screen
 * reader navigates by — one `main`, one `h1` that can be sent focus — and that the optional parts
 * really are optional.
 */

import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuthCard } from "./AuthCard";

describe("AuthCard", () => {
  it("is one main landmark with one level-one heading", () => {
    render(
      <AuthCard brand={<span>OpsGrids</span>} title="Operator sign-in">
        <p>form</p>
      </AuthCard>,
    );
    expect(screen.getAllByRole("main")).toHaveLength(1);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]?.textContent).toBe("Operator sign-in");
  });

  it("renders the brand, the intro, the form and the footer in the card", () => {
    render(
      <AuthCard
        brand={<span>OpsGrids Internal</span>}
        title="Enter your code"
        intro="From your authenticator app."
        footer="Access is logged and audited."
      >
        <button type="button">Verify</button>
      </AuthCard>,
    );
    expect(screen.getByText("OpsGrids Internal")).toBeDefined();
    expect(screen.getByText("From your authenticator app.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Verify" })).toBeDefined();
    expect(screen.getByText("Access is logged and audited.")).toBeDefined();
  });

  it("leaves the intro and the footer out when there are none", () => {
    const { container } = render(
      <AuthCard brand={<span>OpsGrids</span>} title="Sign in">
        <p>form</p>
      </AuthCard>,
    );
    expect(container.querySelectorAll("p")).toHaveLength(1);
  });

  it("lets a step send focus to its heading, and keeps the heading out of the tab order", () => {
    const ref = createRef<HTMLHeadingElement>();
    render(
      <AuthCard brand={<span>OpsGrids</span>} title="Save your recovery codes" titleRef={ref}>
        <p>codes</p>
      </AuthCard>,
    );
    ref.current?.focus();
    expect(document.activeElement).toBe(ref.current);
    expect(ref.current?.getAttribute("tabindex")).toBe("-1");
  });
});
