import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FormField } from "./FormField";
import { PasswordField } from "./PasswordField";

describe("PasswordField", () => {
  it("is labelled, required and hidden by default", () => {
    render(<PasswordField value="" onChange={() => undefined} />);
    const input = screen.getByLabelText("Password") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.required).toBe(true);
    expect(input.getAttribute("autocomplete")).toBe("current-password");
  });

  it("reports what is typed as a string", async () => {
    const onChange = vi.fn();
    render(<PasswordField value="" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Password"), "x");
    expect(onChange).toHaveBeenCalledWith("x");
  });

  it("toggles visibility with a pressed state and keeps the value", async () => {
    render(<PasswordField value="hunter2" onChange={() => undefined} />);
    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(toggle);
    const input = screen.getByLabelText("Password") as HTMLInputElement;
    expect(input.type).toBe("text");
    expect(input.value).toBe("hunter2");
    expect(screen.getByRole("button", { name: "Hide password" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("ties the helper and the error to the input and flags it invalid", () => {
    render(
      <PasswordField
        label="New password"
        autoComplete="new-password"
        value="x"
        onChange={() => undefined}
        helper="At least 12 characters."
        error="Too short."
      />,
    );
    const input = screen.getByLabelText("New password");
    expect(input.getAttribute("autocomplete")).toBe("new-password");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const described = (input.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent);
    expect(described).toEqual(["At least 12 characters.", "Too short."]);
  });
});

describe("FormField controlId", () => {
  it("labels a control that already has an id, instead of minting another", () => {
    render(
      <FormField label="Your name" controlId="existing-id">
        {({ id }) => <input id={id} />}
      </FormField>,
    );
    expect(screen.getByLabelText("Your name").id).toBe("existing-id");
  });
});
