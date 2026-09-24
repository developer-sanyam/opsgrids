import type { Meta, StoryObj } from "@storybook/react-vite";

import { SearchInput } from "./SearchInput";

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

/** The accent ring, for screens where search is the primary control. */
export const AccentFocus: Story = {
  args: { focusRing: "accent" },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("searchbox"));
  },
};
