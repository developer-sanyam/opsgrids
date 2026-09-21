/**
 * Form controls fail quietly: a label that is not tied to its input, a helper that vanishes the
 * moment an error appears, a switch whose consequence is read after the control rather than with
 * it. None of that is visible in a screenshot, so it is pinned here.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CheckboxGroup } from "./CheckboxGroup";
import { InlineAlert } from "./InlineAlert";
import { RadioGroup } from "./RadioGroup";
import { Select } from "./Select";
import { Switch } from "./Switch";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";

function describedText(element: Element): string {
  const ids = (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
  return ids
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ");
}

describe("TextField", () => {
  it("ties its label to its input", () => {
    render(<TextField label="Employee number" />);
    expect(screen.getByLabelText("Employee number")).toBeDefined();
  });

  it("keeps the helper when an error appears — the error appends, it never replaces", () => {
    render(
      <TextField
        label="Employee number"
        helper="Six digits, as printed on the badge."
        error="That number doesn't exist."
      />,
    );
    const input = screen.getByLabelText("Employee number");
    expect(describedText(input)).toContain("Six digits, as printed on the badge.");
    expect(describedText(input)).toContain("That number doesn't exist.");
    expect(input.getAttribute("aria-invalid")).toBe("true");
  });
});

describe("TextArea", () => {
  it("accepts typing and reports it upward", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TextArea label="Comment" value="" onChange={onChange} />);
    await user.type(screen.getByLabelText("Comment"), "ok");
    expect(onChange).toHaveBeenCalled();
  });

  it("shows a counter only near the limit, where it reads as a warning", () => {
    const { rerender } = render(
      <TextArea label="Reason" value={"x".repeat(10)} maxLength={500} counterFrom={400} readOnly />,
    );
    expect(screen.queryByText("10/500")).toBeNull();
    rerender(
      <TextArea label="Reason" value={"x".repeat(430)} maxLength={500} counterFrom={400} readOnly />,
    );
    expect(screen.getByText("430/500")).toBeDefined();
  });

  it("mirrors the server's limit rather than inventing one", () => {
    render(<TextArea label="Reason" value="" maxLength={500} readOnly />);
    expect(screen.getByLabelText("Reason").getAttribute("maxlength")).toBe("500");
  });

  it("separates the label from its optional marker with a real space, not just a margin", () => {
    // The decision dialog's Comment field is exactly this shape. `getByLabelText` with an exact
    // string (not a regex) is the point: the accessible name is computed from text content, and a
    // `margin-left` between the label and "(optional)" is invisible to that computation — before
    // the fix this read as the single run-on word "Comment(optional)".
    render(<TextArea label="Comment" optional value="" readOnly />);
    expect(screen.getByLabelText("Comment (optional)")).toBeDefined();
  });
});

describe("Select", () => {
  it("is a native select with its options", () => {
    render(
      <Select
        label="Sort"
        value="oldest"
        onChange={vi.fn()}
        options={[
          { value: "oldest", label: "Oldest waiting first" },
          { value: "newest", label: "Newest first" },
        ]}
      />,
    );
    expect(screen.getByRole("combobox", { name: "Sort" })).toBeDefined();
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });
});

describe("RadioGroup", () => {
  function Harness() {
    const [value, setValue] = useState<string | null>(null);
    return (
      <RadioGroup
        legend="Change to"
        name="change-to"
        value={value}
        onChange={setValue}
        helper="Pick what the day should say."
        options={[
          { value: "present", label: "Present" },
          { value: "half_day", label: "Half day" },
          { value: "regularized", label: "Regularised" },
        ]}
      />
    );
  }

  it("is a real group, so every option is announced as one of three", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Change to" })).toBeDefined();
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("is reachable and selectable from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    const first = screen.getByRole("radio", { name: /Present/ }) as HTMLInputElement;
    expect(document.activeElement).toBe(first);
    await user.keyboard(" ");
    expect(first.checked).toBe(true);
  });
});

describe("CheckboxGroup", () => {
  const OPTIONS = [
    { value: "finance_admin", label: "Finance admin", description: "Issues invoices and credit notes." },
    { value: "finance_viewer", label: "Finance viewer" },
    { value: "owner", label: "Owner", disabled: true },
  ];

  function Harness({ initial = [] as string[] }: { initial?: string[] }) {
    const [value, setValue] = useState<string[]>(initial);
    return (
      <CheckboxGroup
        label="Roles"
        value={value}
        onChange={setValue}
        options={OPTIONS}
        helper="A member can hold more than one."
      />
    );
  }

  it("is a real group of real checkboxes", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Roles" })).toBeDefined();
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  });

  it("adds and removes, reporting the whole next selection in the options' order", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CheckboxGroup
        label="Roles"
        value={["finance_viewer"]}
        onChange={onChange}
        options={OPTIONS}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: /Finance admin/ }));
    expect(onChange).toHaveBeenCalledWith(["finance_admin", "finance_viewer"]);

    onChange.mockClear();
    await user.click(screen.getByRole("checkbox", { name: /Finance viewer/ }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("keeps a chosen value the current options do not offer, rather than dropping it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CheckboxGroup
        label="Roles"
        value={["role_from_another_page"]}
        onChange={onChange}
        options={OPTIONS}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: /Finance viewer/ }));
    expect(onChange).toHaveBeenCalledWith(["finance_viewer", "role_from_another_page"]);
  });

  it("toggles from the keyboard, and a disabled option is not reachable", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    const first = screen.getByRole("checkbox", { name: /Finance admin/ }) as HTMLInputElement;
    expect(document.activeElement).toBe(first);
    await user.keyboard(" ");
    expect(first.checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: /Owner/ }) as HTMLInputElement).disabled).toBe(true);
  });

  it("describes the group by its helper and its error, and each box by its own description", () => {
    render(
      <CheckboxGroup
        label="Roles"
        value={[]}
        onChange={vi.fn()}
        options={OPTIONS}
        helper="A member can hold more than one."
        error="Pick at least one role."
      />,
    );
    const group = screen.getByRole("group", { name: "Roles" });
    expect(describedText(group)).toContain("A member can hold more than one.");
    expect(describedText(group)).toContain("Pick at least one role.");
    expect(group.getAttribute("aria-invalid")).toBe("true");
    expect(describedText(screen.getByRole("checkbox", { name: /Finance admin/ }))).toContain(
      "Issues invoices and credit notes.",
    );
  });
});

describe("a hidden label", () => {
  it("keeps the control named for a screen reader while taking the label off the screen", () => {
    render(
      <Select
        label="Scope"
        hideLabel
        value="ORG"
        onChange={vi.fn()}
        options={[
          { value: "ORG", label: "Whole organisation" },
          { value: "SELF", label: "Only themselves" },
        ]}
      />,
    );
    const control = screen.getByRole("combobox", { name: "Scope" });
    const label = document.querySelector(`label[for="${control.id}"]`);
    expect(label?.className).toContain("sr-only");
    expect(label?.textContent).toBe("Scope");
  });

  it("is off by default, and the four FormField controls all take it", () => {
    render(
      <>
        <TextField label="Quantity" hideLabel readOnly value="2" />
        <TextArea label="Note" hideLabel readOnly value="" />
        <Select label="Unit" hideLabel value="kg" onChange={vi.fn()} options={[{ value: "kg", label: "kg" }]} />
        <TextField label="Reference" readOnly value="" />
      </>,
    );
    for (const name of ["Quantity", "Note", "Unit"]) {
      const control = screen.getByLabelText(name);
      expect(document.querySelector(`label[for="${control.id}"]`)?.className).toContain("sr-only");
    }
    const visible = screen.getByLabelText("Reference");
    expect(document.querySelector(`label[for="${visible.id}"]`)?.className).not.toContain("sr-only");
  });
});

describe("Switch", () => {
  function Harness() {
    const [on, setOn] = useState(true);
    return (
      <Switch
        checked={on}
        onChange={setOn}
        label="Move requests up when nobody answers"
        description={
          on
            ? "If an approver doesn't answer in time, the request moves up to their manager."
            : "Requests stay with the approver until they answer. Nothing is decided automatically."
        }
      />
    );
  }

  it("is a switch with its state and its consequence, not a bare checkbox", () => {
    render(<Harness />);
    const control = screen.getByRole("switch", { name: "Move requests up when nobody answers" });
    expect(control.getAttribute("aria-checked")).toBe("true");
    expect(describedText(control)).toContain("moves up to their manager");
  });

  it("swaps the consequence text as it toggles, from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const control = screen.getByRole("switch", { name: "Move requests up when nobody answers" });
    control.focus();
    await user.keyboard(" ");
    expect(control.getAttribute("aria-checked")).toBe("false");
    expect(describedText(control)).toContain("Nothing is decided automatically");
  });
});

describe("InlineAlert", () => {
  it("announces a failure and merely states everything else", () => {
    const { rerender } = render(<InlineAlert tone="danger" title="Already decided" />);
    expect(screen.getByRole("alert")).toBeDefined();
    rerender(<InlineAlert tone="warning" title="Approved, but the correction couldn't be applied." />);
    expect(screen.getByRole("status")).toBeDefined();
  });

  it("stays silent for a state that is simply present on load", () => {
    render(<InlineAlert tone="warning" live="off" title="4 requests are overdue." />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("4 requests are overdue.")).toBeDefined();
  });

  it("carries the problem's reference where support can read it back", () => {
    render(<InlineAlert tone="danger" title="Too many attempts" reference="7f3c1a2e" />);
    expect(screen.getByText("Reference: 7f3c1a2e")).toBeDefined();
  });
});
