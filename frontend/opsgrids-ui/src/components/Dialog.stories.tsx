import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Timeline } from "./Timeline";

const FOOTER = (
  <>
    <Button variant="ghost">Close</Button>
    <Button>Approve request</Button>
  </>
);

const HISTORY = Array.from({ length: 14 }, (_, index) => ({
  id: `h-${index}`,
  content: `Level ${index + 1} approver reviewed the request and passed it on.`,
  timestamp: `${index + 3} Sep, 10:${String(index * 4).padStart(2, "0")} am IST`,
  tone: index === 13 ? ("positive" as const) : ("neutral" as const),
}));

const meta = {
  title: "Overlays/Dialog",
  component: Dialog,
  tags: ["viewport"],
  args: {
    open: true,
    onClose: fn(),
    title: "Leave request from Priya Raman",
    description: "3 days of earned leave, 22–24 Sep. Her balance after this request is 9 days.",
    footer: FOOTER,
  },
} satisfies Meta<typeof Dialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    children: (
      <p className="text-sm text-ink-soft">
        Nobody else on the Payments team is away those days. Approving notifies Priya and her
        manager.
      </p>
    ),
  },
};

export const Small: Story = {
  args: { size: "sm", title: "Sign out everywhere?", description: "Every device signed in to this account is signed out.", children: undefined },
};

/** Content taller than the viewport scrolls inside the panel; the footer stays in reach. */
export const LongContent: Story = {
  args: { size: "lg", children: <Timeline entries={HISTORY} /> },
};

export const WithoutFooter: Story = {
  args: {
    footer: undefined,
    description: undefined,
    title: "Keyboard shortcuts",
    children: (
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <dt className="text-muted">Search</dt>
        <dd className="font-semibold">⌘ K</dd>
        <dt className="text-muted">New record</dt>
        <dd className="font-semibold">N</dd>
      </dl>
    ),
  },
};
