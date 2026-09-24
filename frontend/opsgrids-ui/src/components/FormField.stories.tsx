import type { Meta, StoryObj } from "@storybook/react-vite";

import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

/**
 * `FormField` is the wiring every other field is built on. These stories give it a bare
 * `<input>` so what is on screen is the field's own label, helper and error treatment.
 */
const meta = {
  title: "Forms/FormField",
  component: FormField,
  args: {
    label: "GSTIN",
    helper: "15 characters, e.g. 29ABCDE1234F1Z5.",
    children: ({ id, describedBy, invalid }) => (
      <input
        id={id}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        defaultValue="29ABCDE1234F1Z5"
        className={fieldSkin(invalid, "h-11")}
      />
    ),
  },
  render: (args) => (
    <div className="max-w-sm">
      <FormField {...args} />
    </div>
  ),
} satisfies Meta<typeof FormField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithHelper: Story = {};

/** The platform labels the optional, not the required. */
export const Optional: Story = { args: { optional: true, label: "Purchase order number", helper: undefined } };

/** The error appends — the helper stays, because it says what a valid value looks like. */
export const WithError: Story = { args: { error: "That GSTIN's checksum doesn't match." } };

export const LabelAside: Story = {
  args: { labelAside: <span className="text-xs tnum text-muted">12/15</span> },
};

/** Label kept for screen readers, off the screen — for a dense row whose column already says it. */
export const HiddenLabel: Story = { args: { hideLabel: true, helper: undefined } };
