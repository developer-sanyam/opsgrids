import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Combobox } from "./Combobox";
import type { ComboboxOption } from "./Combobox";

const PEOPLE: ComboboxOption[] = [
  { id: "emp-1", label: "Arjun Mehta", sublabel: "EMP-0142" },
  { id: "emp-2", label: "Aarti Sharma", sublabel: "EMP-0219" },
  { id: "emp-3", label: "Karan Arora", sublabel: "EMP-0307" },
  { id: "emp-4", label: "Priya Raman", sublabel: "EMP-0088" },
  { id: "emp-5", label: "Sofia Marchetti", sublabel: "EMP-0011" },
];

/** The story's stand-in for the search endpoint: answers at once, from a fixed list, no network. */
function search(query: string): Promise<ComboboxOption[]> {
  const needle = query.toLowerCase();
  return Promise.resolve(PEOPLE.filter((person) => person.label.toLowerCase().includes(needle)));
}

function Stateful(props: ComponentProps<typeof Combobox>) {
  const [value, setValue] = useState(props.value);
  return (
    <Combobox
      {...props}
      value={value}
      onChange={(next) => {
        setValue(next);
        props.onChange(next);
      }}
    />
  );
}

const meta = {
  title: "Forms/Combobox",
  component: Combobox,
  args: {
    label: "Delegate",
    value: null,
    onChange: fn(),
    loadOptions: search,
    helper: "Search by name or employee number.",
    placeholder: "Search people",
    debounceMs: 0,
  },
  render: (args) => (
    <div className="max-w-md">
      <Stateful {...args} />
    </div>
  ),
} satisfies Meta<typeof Combobox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};

export const Selected: Story = { args: { value: PEOPLE[4] ?? null } };

export const WithError: Story = { args: { error: "Choose someone other than yourself." } };

export const Disabled: Story = { args: { disabled: true, value: PEOPLE[0] ?? null } };

/** Typed, matched, first option active — the state a keyboard user drives with the arrows. */
export const Results: Story = {
  tags: ["viewport"],
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "ar");
    await canvas.findByRole("option", { name: /Arjun Mehta/ });
  },
};

export const TooShort: Story = {
  tags: ["viewport"],
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "a");
    await canvas.findByText("Keep typing — at least two letters.");
  },
};

export const NoMatches: Story = {
  tags: ["viewport"],
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "zz");
    await canvas.findByText("No one matches “zz”.");
  },
};

/** A request that has not answered yet. */
export const Searching: Story = {
  tags: ["viewport"],
  args: { loadOptions: () => new Promise<ComboboxOption[]>(() => undefined) },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "ar");
    await canvas.findByText("Searching…");
  },
};

export const LoadFailed: Story = {
  tags: ["viewport"],
  args: { loadOptions: () => Promise.reject(new Error("503")) },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "ar");
    await canvas.findByRole("button", { name: "Try again" });
  },
};

/** Exactly `cap` results came back, so the footer says the list is truncated. */
export const Capped: Story = {
  tags: ["viewport"],
  args: { cap: 3, loadOptions: () => Promise.resolve(PEOPLE.slice(0, 3)) },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "ar");
    await canvas.findByText("Showing the first 3. Keep typing to narrow it down.");
  },
};
