import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Button } from "./Button";
import { Drawer } from "./Drawer";

function AccountSummary() {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
      <dt className="text-muted">Domain</dt>
      <dd className="text-ink">acme.in</dd>
      <dt className="text-muted">Plan</dt>
      <dd className="text-ink">Enterprise</dd>
      <dt className="text-muted">Users</dt>
      <dd className="tnum text-ink">24</dd>
      <dt className="text-muted">Monthly revenue</dt>
      <dd className="tnum text-ink">₹49,800</dd>
      <dt className="text-muted">Owner</dt>
      <dd className="text-ink">Priya Raman</dd>
      <dt className="text-muted">Created</dt>
      <dd className="text-ink">12 Aug 2026</dd>
    </dl>
  );
}

function History() {
  return (
    <ol className="flex flex-col gap-4">
      {Array.from({ length: 14 }, (_, index) => (
        <li key={index} className="flex flex-col gap-0.5 border-b border-line pb-4 last:border-0">
          <span className="text-sm text-ink">Plan usage checked: {60 + index}% of the monthly allowance.</span>
          <span className="text-xs text-muted">
            {index + 1} Sep, 09:{String(index * 4).padStart(2, "0")} am IST
          </span>
        </li>
      ))}
    </ol>
  );
}

const meta = {
  title: "Overlays/Drawer",
  component: Drawer,
  tags: ["viewport"],
  args: {
    open: true,
    onClose: fn(),
    title: "Acme Pvt Ltd",
    description: "Enterprise · Active since 12 Aug 2026",
    children: <AccountSummary />,
    footer: (
      <>
        <Button variant="ghost">Close</Button>
        <Button variant="dark">Open account</Button>
      </>
    ),
  },
} satisfies Meta<typeof Drawer>;

export default meta;
type Story = StoryObj<typeof meta>;

/** From the right at 768px and up; a bottom sheet on a phone. */
export const Right: Story = {};

export const Left: Story = { args: { side: "left" } };

export const Large: Story = { args: { size: "lg" } };

/** The body scrolls under a fixed header and footer; it is a Tab stop, so a keyboard can scroll it. */
export const LongContent: Story = {
  args: { title: "Activity", description: "Everything that happened on this account.", children: <History /> },
};

export const WithoutFooter: Story = {
  args: { footer: undefined, description: undefined, title: "Keyboard shortcuts", size: "sm", children: <AccountSummary /> },
};
