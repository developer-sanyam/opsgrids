/**
 * The required-marker convention (owner ruling, 2026-09-25, widened 2026-09-25 to every form): a
 * required `TextField`, `TextArea` or `Select` gets a red asterisk whether or not it sits inside a
 * `FormSection`. The asterisk must never change what the field is called — that name is what a
 * screen reader reads and what every app's tests look up.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { FormField } from "./FormField";
import { FormSection } from "./FormSection";
import { Select } from "./Select";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";

function asterisks(): number {
  return Array.from(document.querySelectorAll("[aria-hidden]")).filter((node) => node.textContent === "*").length;
}

describe("FormSection", () => {
  it("is a section named by its heading, at the level asked for", () => {
    render(
      <FormSection title="Pricing & stock" description="What it costs and how many you have." headingLevel={3}>
        <TextField label="Selling price" />
      </FormSection>,
    );
    expect(screen.getByRole("region", { name: "Pricing & stock" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Pricing & stock" })).toBeTruthy();
  });

  it("marks each required text field, text area and select with an asterisk", () => {
    render(
      <FormSection title="Basic information">
        <TextField label="Product name" required />
        <TextArea label="Short description" required />
        <Select label="Category" required options={[{ value: "bags", label: "Bags" }]} />
        <TextField label="Barcode" optional />
      </FormSection>,
    );
    expect(asterisks()).toBe(3);
  });

  it("leaves the accessible name alone and keeps the control's own required state", () => {
    render(
      <FormSection title="Basic information">
        <TextField label="Product name" required />
      </FormSection>,
    );
    const input = screen.getByLabelText("Product name");
    expect(input).toHaveProperty("required", true);
    expect(screen.getByRole("textbox", { name: "Product name" })).toBe(input);
  });

  it("marks a required field outside a section too — the asterisk isn't tied to being here", () => {
    render(<TextField label="Deal name" required />);
    expect(asterisks()).toBe(1);
    expect(screen.getByLabelText("Deal name")).toHaveProperty("required", true);
  });

  it("draws nothing for a field that isn't required, in or out of a section", () => {
    render(<TextField label="Nickname" />);
    expect(asterisks()).toBe(0);
  });
});

describe("FormField required", () => {
  it("marks the label and tells the control it is required", () => {
    render(
      <FormField label="Colour" required>
        {({ id, required }) => <input id={id} aria-required={required || undefined} />}
      </FormField>,
    );
    expect(asterisks()).toBe(1);
    expect(screen.getByRole("textbox", { name: "Colour" }).getAttribute("aria-required")).toBe("true");
  });
});
