import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./Button";
import { InlineAlert } from "./InlineAlert";
import type { InlineAlertTone } from "./InlineAlert";

const TONES: { tone: InlineAlertTone; title: string }[] = [
  { tone: "info", title: "Payroll for September locks on the 28th." },
  { tone: "warning", title: "3 requests are overdue for a decision." },
  { tone: "danger", title: "The bank rejected this payout." },
  { tone: "success", title: "Approved. The correction was applied to the attendance record." },
];

const meta = {
  title: "Feedback/InlineAlert",
  component: InlineAlert,
  args: { tone: "info", title: "Payroll for September locks on the 28th.", live: "off" },
  render: (args) => (
    <div className="max-w-2xl">
      <InlineAlert {...args} />
    </div>
  ),
} satisfies Meta<typeof InlineAlert>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="flex max-w-2xl flex-col gap-3">
      {TONES.map(({ tone, title }) => (
        <InlineAlert key={tone} tone={tone} title={title} live="off" />
      ))}
    </div>
  ),
};

export const WithBodyActionAndReference: Story = {
  args: {
    tone: "danger",
    title: "Permissions couldn't be checked — some actions may be hidden",
    children: "Create, edit and delete controls are hidden until this succeeds. Reloading the page also retries it.",
    action: (
      <Button size="sm" variant="outline">
        Try again
      </Button>
    ),
    reference: "7f3c1a2e",
  },
};

export const LongText: Story = {
  args: {
    tone: "warning",
    title:
      "You already have a delegation that covers 20–27 September for approvals in Payments, Treasury, Procurement and every cost centre under the West region",
    children:
      "Revoke it first, or shorten this one so the two do not overlap. Two delegations for the same approvals at once would leave nobody sure who decided.",
  },
};
