import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { ProductLoginScreen } from "./ProductLoginScreen";

function props(overrides: Partial<ComponentProps<typeof ProductLoginScreen>> = {}) {
  return {
    brand: "OpsGrids Finance",
    logo: <span>logo</span>,
    tagline: "Invoices, payments and books that reconcile themselves.",
    step: "credentials" as const,
    email: "",
    onEmailChange: vi.fn(),
    password: "",
    onPasswordChange: vi.fn(),
    submitting: false,
    error: null,
    onSubmit: vi.fn(),
    onPickOrganization: vi.fn(),
    ...overrides,
  };
}

describe("ProductLoginScreen", () => {
  it("names the product in the heading and the footer, with the tagline beneath", () => {
    render(<ProductLoginScreen {...props()} />);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in to OpsGrids Finance" })).toBeTruthy();
    expect(screen.getByText("Invoices, payments and books that reconcile themselves.")).toBeTruthy();
    expect(screen.getByText("OpsGrids Finance · secured by your organization's single sign-on")).toBeTruthy();
  });

  it("lets a product word the heading and the footer differently from its brand", () => {
    render(<ProductLoginScreen {...props({ brand: "OpsGrids HR", headingName: "People", footerName: "OpsGrids HRMS" })} />);
    expect(screen.getByRole("heading", { name: "Sign in to People" })).toBeTruthy();
    expect(screen.getByText(/OpsGrids HRMS · secured/)).toBeTruthy();
  });

  it("ties a label to each field and marks both required", () => {
    render(<ProductLoginScreen {...props()} />);
    const email = screen.getByLabelText("Work email") as HTMLInputElement;
    expect(email.type).toBe("email");
    expect(email.required).toBe(true);
    expect(email.getAttribute("autocomplete")).toBe("email");
    const password = screen.getByLabelText("Password") as HTMLInputElement;
    expect(password.type).toBe("password");
    expect(password.getAttribute("autocomplete")).toBe("current-password");
  });

  it("reports typing and submits through the form", async () => {
    const onEmailChange = vi.fn();
    const onSubmit = vi.fn();
    render(<ProductLoginScreen {...props({ onEmailChange, onSubmit })} />);
    await userEvent.type(screen.getByLabelText("Work email"), "a");
    expect(onEmailChange).toHaveBeenCalledWith("a");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("shows and hides the password without losing it", async () => {
    render(<ProductLoginScreen {...props({ password: "secret" })} />);
    const field = screen.getByLabelText("Password") as HTMLInputElement;
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(field.type).toBe("text");
    expect(field.value).toBe("secret");
    await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(field.type).toBe("password");
  });

  it("disables and indicates while submitting, and does not submit twice", async () => {
    const onSubmit = vi.fn();
    render(<ProductLoginScreen {...props({ submitting: true, onSubmit })} />);
    const button = screen.getByRole("button", { name: "Signing in…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    await userEvent.type(screen.getByLabelText("Work email"), "{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("announces an error as an alert", () => {
    render(<ProductLoginScreen {...props({ error: "That email or password is incorrect." })} />);
    expect(screen.getByRole("alert").textContent).toContain("That email or password is incorrect.");
  });

  it("offers the dev demo fill only when given one", async () => {
    const onFill = vi.fn();
    const { rerender } = render(<ProductLoginScreen {...props()} />);
    expect(screen.queryByText("Demo access")).toBeNull();
    rerender(<ProductLoginScreen {...props({ demo: { email: "admin@demo.example.com", onFill } })} />);
    await userEvent.click(screen.getByRole("button", { name: /Demo access/ }));
    expect(onFill).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/admin@demo.example.com/)).toBeTruthy();
  });

  describe("choosing an organization", () => {
    const organizations = [
      { organization_id: "org-a", name: "Acme India", roles: ["admin", "billing"] },
      { organization_id: "org-b", name: "Acme UK", roles: ["viewer"] },
    ];

    it("lists each organization with its roles and reports the pick", async () => {
      const onPickOrganization = vi.fn();
      render(<ProductLoginScreen {...props({ step: "organizations", organizations, onPickOrganization })} />);
      expect(screen.getByRole("heading", { name: "Choose an organization" })).toBeTruthy();
      expect(screen.getByText("admin · billing")).toBeTruthy();
      await userEvent.click(screen.getByRole("button", { name: /Acme UK/ }));
      expect(onPickOrganization).toHaveBeenCalledWith("org-b");
    });

    it("disables the choices while one is being finished", () => {
      render(<ProductLoginScreen {...props({ step: "organizations", organizations, submitting: true })} />);
      for (const button of screen.getAllByRole("button")) expect((button as HTMLButtonElement).disabled).toBe(true);
    });

    it("moves focus to the new heading when the step changes, and not on first render", () => {
      const { rerender } = render(<ProductLoginScreen {...props()} />);
      expect(document.activeElement).toBe(document.body);
      rerender(<ProductLoginScreen {...props({ step: "organizations", organizations })} />);
      expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Choose an organization" }));
    });
  });
});
