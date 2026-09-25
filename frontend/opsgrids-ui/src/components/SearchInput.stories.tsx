import { useState } from "react";
import type { ComponentProps } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { SearchInput } from "./SearchInput";

function DebouncedDemo(props: ComponentProps<typeof SearchInput>) {
  const [query, setQuery] = useState("");
  return (
    <div className="flex max-w-md flex-col gap-2">
      <SearchInput {...props} value={query} onDebouncedChange={setQuery} />
      <p className="text-sm text-muted">{query === "" ? "Nothing searched yet" : `Last search: ${query}`}</p>
    </div>
  );
}

const meta = {
  title: "Forms/SearchInput",
  component: SearchInput,
  args: { label: "Search contacts", placeholder: "Search by name, email or company" },
  render: (args) => (
    <div className="max-w-md">
      <SearchInput {...args} />
    </div>
  ),
} satisfies Meta<typeof SearchInput>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};

export const Filled: Story = { args: { defaultValue: "priya@acme" } };

export const Disabled: Story = { args: { disabled: true } };

/**
 * `onDebouncedChange`: the query is reported once typing pauses, not per keystroke. The line
 * under the box shows the last query a list would have fetched.
 */
export const Debounced: Story = {
  render: (args) => <DebouncedDemo {...args} />,
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("searchbox"), "acme");
    await canvas.findByText("Last search: acme");
  },
};

/** The accent ring, for screens where search is the primary control. */
export const AccentFocus: Story = {
  args: { focusRing: "accent" },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("searchbox"));
  },
};
