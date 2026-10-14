/**
 * A stepper answers "where am I in this form" in words as well as dots, and only offers the steps
 * a user may go back to — forward is Continue's job, because Continue is where a step is checked.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Stepper, StepperActions } from "./Stepper";
import type { StepItem } from "./Stepper";

const STEPS: StepItem[] = [
  { id: "basic", title: "Basic information", description: "Name, description, category" },
  { id: "pricing", title: "Pricing & stock", description: "Price, stock, SKU" },
  { id: "media", title: "Media", description: "Images and documents" },
  { id: "details", title: "Additional details", description: "SKU, barcode, tax" },
];

describe("Stepper", () => {
  it("is an ordered list in a named nav, with the current step marked", () => {
    render(<Stepper steps={STEPS} current={1} />);
    const nav = screen.getByRole("navigation", { name: "Form progress" });
    const items = within(nav).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[1]?.getAttribute("aria-current")).toBe("step");
    expect(items[0]?.textContent).toContain("Completed");
    expect(items[2]?.textContent).toContain("Not started");
  });

  it("says how far along the form is, for the phone layout", () => {
    render(<Stepper steps={STEPS} current={1} />);
    expect(screen.getByText("Step 2 of 4")).toBeTruthy();
  });

  it("offers the steps already reached as buttons, and not the ones ahead", async () => {
    const user = userEvent.setup();
    const onStepChange = vi.fn();
    render(<Stepper steps={STEPS} current={1} reached={2} onStepChange={onStepChange} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /Basic information/ }));
    expect(onStepChange).toHaveBeenCalledWith(0);
    await user.click(screen.getByRole("button", { name: /Media/ }));
    expect(onStepChange).toHaveBeenCalledWith(2);
  });

  it("offers no buttons at all when it is only an indicator", () => {
    render(<Stepper steps={STEPS} current={2} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("marks a step that has errors in words", () => {
    render(<Stepper steps={STEPS.map((step, index) => (index === 0 ? { ...step, invalid: true } : step))} current={1} />);
    expect(screen.getAllByRole("listitem")[0]?.textContent).toContain("Has errors");
  });
});

describe("StepperActions", () => {
  it("has no Back on the first step and submits the form on Continue", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <StepperActions />
      </form>,
    );
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("goes back without submitting", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    const onBack = vi.fn();
    render(
      <form onSubmit={onSubmit}>
        <StepperActions onBack={onBack} />
      </form>,
    );
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("holds both buttons while the final step saves", () => {
    render(<StepperActions onBack={vi.fn()} final continueLabel="Save product" pending pendingLabel="Saving…" />);
    expect(screen.getByRole("button", { name: "Back" })).toHaveProperty("disabled", true);
    const save = screen.getByRole("button", { name: "Saving…" });
    expect(save).toHaveProperty("disabled", true);
    expect(save.getAttribute("aria-busy")).toBe("true");
  });

  it("sticks above the bottom navigation on a phone, not behind it", () => {
    render(<StepperActions />);
    const bar = screen.getByRole("button", { name: "Continue" }).parentElement;
    expect(bar?.className).toContain("sticky");
    expect(bar?.style.bottom).toBe("var(--shell-bottom-inset)");
  });

  it("runs onContinue instead of submitting when given one", async () => {
    const user = userEvent.setup();
    const onContinue = vi.fn();
    render(<StepperActions onContinue={onContinue} />);
    const next = screen.getByRole("button", { name: "Continue" });
    expect(next.getAttribute("type")).toBe("button");
    await user.click(next);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});
