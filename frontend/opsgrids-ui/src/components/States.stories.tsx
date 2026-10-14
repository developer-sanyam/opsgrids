import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { InboxIcon, PlusIcon } from "../icons";
import { Button } from "./Button";
import { Card } from "./Card";
import { EmptyState, ErrorState, SectionHeader, Skeleton } from "./States";

/**
 * The states that are not the happy path. Every list has all three: empty says what to do next,
 * error says what went wrong and how to get out of it, loading holds the layout still.
 */
const meta = {
  title: "Feedback/States",
  component: EmptyState,
  subcomponents: { ErrorState, Skeleton, SectionHeader },
  args: {
    icon: <InboxIcon className="size-6" />,
    title: "No contacts yet",
    description: "Contacts you add, import or convert from a lead appear here.",
    action: (
      <Button size="sm" leadingIcon={<PlusIcon className="size-4" />}>
        Add a contact
      </Button>
    ),
  },
} satisfies Meta<typeof EmptyState>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};

export const EmptyMinimal: Story = {
  args: { icon: undefined, action: undefined, title: "Nothing is overdue", description: "Every request due today has a decision." },
};

export const Failure: Story = {
  render: () => <ErrorState message="The deals service didn't answer. Your data is safe." reference="7f3c1a2e" onRetry={fn()} />,
};

/** A 403 is a different fact from a 500, and says so. */
export const ErrorForbidden: Story = {
  render: () => (
    <ErrorState
      tone="warning"
      title="You can't view payroll"
      message="Ask an HR admin for the Payroll viewer role."
      action={
        <Button size="sm" variant="outline">
          Back to people
        </Button>
      }
    />
  ),
};

export const ErrorNeutral: Story = {
  render: () => <ErrorState tone="neutral" title="That request isn't available" message="It may have been withdrawn by the person who raised it." />,
};

/** Skeletons reserve the space the content will take, so nothing moves when it lands. */
export const Loading: Story = {
  render: () => (
    <Card className="flex max-w-xl flex-col gap-4 p-5">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
      <div className="grid grid-cols-3 gap-3">
        <Skeleton className="h-20 rounded-tile" />
        <Skeleton className="h-20 rounded-tile" />
        <Skeleton className="h-20 rounded-tile" />
      </div>
    </Card>
  ),
};

export const SectionHeaders: Story = {
  render: () => (
    <Card className="flex max-w-xl flex-col gap-6 p-5">
      <SectionHeader title="Pipeline by stage" />
      <SectionHeader
        title="Needs attention"
        subtitle="Deals with no activity in 14 days"
        action={
          <Button size="sm" variant="ghost">
            View all
          </Button>
        }
      />
    </Card>
  ),
};

/** A raw HTTP message from a client is shown as words a person can act on. */
export const ErrorFromHttpStatus: Story = {
  render: () => <ErrorState message="Request failed with 500" onRetry={fn()} />,
};

/** The placeholder that is announced: `label` makes the block a status region. */
export const SkeletonAnnounced: Story = {
  render: () => <Skeleton label="Loading invoices" className="h-16 w-full rounded-card" />,
};
