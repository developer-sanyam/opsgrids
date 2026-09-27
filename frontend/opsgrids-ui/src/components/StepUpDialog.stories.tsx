import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, fn, screen, waitFor } from "storybook/test";

import { StepUpDialog } from "./StepUpDialog";
import type { StepUpDialogProps } from "./StepUpDialog";

/** The host's half of a submit, reduced to what the dialog can see: a code goes out and it is busy. */
function BusyOnSubmit(args: StepUpDialogProps) {
  const [busy, setBusy] = useState(false);
  return (
    <StepUpDialog
      {...args}
      busy={busy}
      onSubmit={(code) => {
        args.onSubmit(code);
        setBusy(true);
      }}
    />
  );
}

const meta = {
  title: "Overlays/StepUpDialog",
  component: StepUpDialog,
  tags: ["viewport"],
  args: {
    open: true,
    busy: false,
    mfaState: "ready",
    onSubmit: fn(),
    onCancel: fn(),
    securityHref: "https://app.opsgrids.com/account/security",
  },
} satisfies Meta<typeof StepUpDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Idle: the prompt, an empty field with focus in it, Verify and Cancel. */
export const Ready: Story = {};

/** The sixth digit submits, so a typed code lands here on its own: read-only field, Verify working. */
export const Verifying: Story = {
  render: (args) => <BusyOnSubmit {...args} />,
  play: async ({ userEvent }) => {
    const field = await screen.findByRole("textbox", { name: /6-digit code/ });
    await waitFor(() => expect(document.activeElement).toBe(field));
    await userEvent.type(field, "482913");
    await screen.findByRole("button", { name: "Verifying…" });
  },
};

/** Enter on a code that is not six digits long says so on the field and sends nothing. */
export const IncompleteCode: Story = {
  play: async ({ userEvent }) => {
    const field = await screen.findByRole("textbox", { name: /6-digit code/ });
    await waitFor(() => expect(document.activeElement).toBe(field));
    await userEvent.type(field, "4829{Enter}");
    await screen.findByText("Enter all 6 digits.");
  },
};

/** Codes are single-use, so the refused digits are gone and the field waits for the next code. */
export const WrongCode: Story = { args: { error: "wrong-code" } };

export const LockedOut: Story = { args: { error: "locked" } };

/** A dropped connection says nothing about the code, so the person is told to retry, not to retype. */
export const Unavailable: Story = { args: { error: "unavailable" } };

/** No second factor on the account: no field, and the Security page is one link away. */
export const NotEnrolled: Story = { args: { mfaState: "not-enrolled" } };

/** A single sign-on session has no factor of ours to check. Nothing to type, nowhere to go. */
export const SingleSignOn: Story = { args: { mfaState: "sso" } };
