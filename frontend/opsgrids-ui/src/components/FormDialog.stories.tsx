import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, fn, screen, waitFor } from "storybook/test";

import { FormDialog } from "./FormDialog";
import { Select } from "./Select";
import { TextArea } from "./TextArea";
import { TextField } from "./TextField";

function Fields({ errors = {} }: { errors?: { name?: string; email?: string } }) {
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="First name" defaultValue="Priya" />
        <TextField label="Last name" defaultValue="Raman" />
      </div>
      <TextField
        label="Work email"
        type="email"
        defaultValue="priya@acme"
        {...(errors.email !== undefined ? { error: errors.email } : {})}
      />
      <Select
        label="Company"
        defaultValue="acme"
        options={[
          { value: "acme", label: "Acme Industries" },
          { value: "bluefin", label: "Bluefin Logistics" },
        ]}
      />
      <TextArea label="Notes" optional rows={3} />
    </>
  );
}

const meta = {
  title: "Overlays/FormDialog",
  component: FormDialog,
  tags: ["viewport"],
  args: {
    open: true,
    onClose: fn(),
    onSubmit: fn(),
    title: "New contact",
    description: "Contacts belong to a company. You can change it later.",
    submitLabel: "Create contact",
    children: <Fields />,
  },
} satisfies Meta<typeof FormDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Saving: Story = { args: { pending: true, pendingLabel: "Creating…" } };

/** A 422 lands on the field that caused it — never as a banner. */
export const FieldErrors: Story = {
  args: { children: <Fields errors={{ email: "Enter a complete email address, like name@company.com." }} /> },
};

/** A 409/403/500 belongs to no one field, so it sits above the actions. */
export const ServerError: Story = {
  args: {
    error: "A contact with this email already exists at Acme Industries.",
    reference: "c0ffee42",
  },
};

/** `dirty`, then Escape: the discard question stacks over the form, focus on Keep editing. */
export const UnsavedChanges: Story = {
  args: { dirty: true },
  play: async ({ userEvent }) => {
    // The dialog's keyboard handling is installed in the same effect that moves focus into it, so
    // Escape is pressed only once focus has arrived — before then it would reach nothing.
    const form = await screen.findByRole("dialog", { name: "New contact" });
    await waitFor(() => expect(form.contains(document.activeElement)).toBe(true));
    await userEvent.keyboard("{Escape}");
    const question = await screen.findByRole("dialog", { name: "Discard unsaved changes?" });
    await waitFor(() => expect(question.contains(document.activeElement)).toBe(true));
  },
};
