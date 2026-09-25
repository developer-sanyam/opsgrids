import { useEffect, useRef } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { useToast } from "../lib/toast";
import type { ToastOptions, ToastTone } from "../lib/toast";
import { Button } from "./Button";
import { Toaster } from "./Toaster";

interface Raise {
  tone: ToastTone;
  title: string;
  options?: ToastOptions;
}

/**
 * Raises its toasts once, on mount, and pinned (`duration: null`) so they hold still for a
 * screenshot. The button raises another, for trying the stack by hand.
 */
function Raiser({ toasts }: { toasts: Raise[] }) {
  const toast = useToast();
  const raised = useRef(false);
  useEffect(() => {
    if (raised.current) return;
    raised.current = true;
    for (const { tone, title, options } of toasts) toast[tone](title, { duration: null, ...options });
  }, [toast, toasts]);
  return (
    <Button variant="outline" onClick={() => toast.success("Contact created.")}>
      Raise a toast
    </Button>
  );
}

const meta = {
  title: "Feedback/Toaster",
  component: Toaster,
  tags: ["viewport"],
  args: { children: null },
} satisfies Meta<typeof Toaster>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Success: Story = {
  render: () => (
    <Toaster>
      <Raiser toasts={[{ tone: "success", title: "Contact created." }]} />
    </Toaster>
  ),
};

export const Failure: Story = {
  render: () => (
    <Toaster>
      <Raiser
        toasts={[
          {
            tone: "error",
            title: "The invoice couldn't be sent.",
            options: { description: "The customer's email address bounced last week.", reference: "7f3c1a2e" },
          },
        ]}
      />
    </Toaster>
  ),
};

export const Info: Story = {
  render: () => (
    <Toaster>
      <Raiser toasts={[{ tone: "info", title: "Export started.", options: { description: "We'll email you when it's ready." } }]} />
    </Toaster>
  ),
};

export const Warning: Story = {
  render: () => (
    <Toaster>
      <Raiser
        toasts={[
          {
            tone: "warning",
            title: "Invoice sent.",
            options: { description: "Payment reminders are off: the customer has no email on file." },
          },
        ]}
      />
    </Toaster>
  ),
};

/** The platform speaking, not the user's action — a notice that outlives a single screen. */
export const System: Story = {
  render: () => (
    <Toaster>
      <Raiser
        toasts={[
          {
            tone: "system",
            title: "Scheduled maintenance tonight.",
            options: { description: "OpsGrids is read-only from 23:00 to 23:30 IST." },
          },
        ]}
      />
    </Toaster>
  ),
};

/** All five tones, each with its own icon, edge and spoken word. */
export const AllTones: Story = {
  render: () => (
    <Toaster>
      <Raiser
        toasts={[
          { tone: "success", title: "Product created." },
          { tone: "info", title: "Export started.", options: { description: "We'll email you when it's ready." } },
          { tone: "warning", title: "Stock is below the reorder level for 3 items." },
          { tone: "system", title: "You're offline.", options: { description: "Changes save when you reconnect." } },
          { tone: "error", title: "Couldn't reach the payments service.", options: { reference: "c0ffee42" } },
        ]}
      />
    </Toaster>
  ),
};

export const Stacked: Story = {
  render: () => (
    <Toaster>
      <Raiser
        toasts={[
          { tone: "success", title: "Deal moved to Negotiation." },
          { tone: "info", title: "Export started.", options: { description: "We'll email you when it's ready." } },
          { tone: "error", title: "Couldn't reach the payments service.", options: { reference: "c0ffee42" } },
        ]}
      />
    </Toaster>
  ),
};
