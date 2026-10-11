import type { Meta, StoryObj } from "@storybook/react-vite";

import { Button } from "./Button";
import { Timeline } from "./Timeline";
import type { TimelineEntry } from "./Timeline";

const ENTRIES: TimelineEntry[] = [
  {
    id: "e1",
    content: "Priya Raman requested 3 days of earned leave.",
    tone: "neutral",
    timestamp: "12 Sep, 9:14 am IST · 3 days ago",
    dateTime: "2026-09-12T03:44:00Z",
  },
  {
    id: "e2",
    content: "Arjun Mehta approved it as her manager.",
    tone: "positive",
    timestamp: "12 Sep, 11:02 am IST · 3 days ago",
    dateTime: "2026-09-12T05:32:00Z",
    quote: "Fine by me — Kwame covers the payments on-call that week.",
  },
  {
    id: "e3",
    content: "The request waited 48 hours for HR and was escalated.",
    tone: "late",
    timestamp: "14 Sep, 11:02 am IST · yesterday",
    dateTime: "2026-09-14T05:32:00Z",
  },
  {
    id: "e4",
    content: "Lena Fischer rejected it on behalf of HR.",
    tone: "negative",
    timestamp: "15 Sep, 10:05 am IST · 25 minutes ago",
    dateTime: "2026-09-15T04:35:00Z",
    quote: "The dates overlap the quarter-end close. Can we move this a week later?",
  },
];

const LADDER: TimelineEntry[] = Array.from({ length: 12 }, (_, index) => ({
  id: `rung-${index}`,
  content: `Level ${index + 1}: escalated to the next approver.`,
  tone: index === 11 ? "accent" : "info",
  timestamp: `${index + 1} Sep, 9:00 am IST`,
}));

const meta = {
  title: "Data display/Timeline",
  component: Timeline,
  args: { entries: ENTRIES },
  render: (args) => (
    <div className="max-w-xl">
      <Timeline {...args} />
    </div>
  ),
} satisfies Meta<typeof Timeline>;

export default meta;
type Story = StoryObj<typeof meta>;

export const History: Story = {};

/** A long history shows its recent end first, with the rest one click away. */
export const Collapsed: Story = { args: { entries: LADDER, collapseAfter: 3, expandLabel: (total: number) => `Show all ${total} levels` } };

export const Empty: Story = { args: { entries: [], emptyMessage: "No decisions yet. The first approver was notified 5 minutes ago." } };

/** A feed, newest first: the head stays, the expander and a "Load more" sit underneath. */
export const NewestFirst: Story = {
  args: {
    entries: [...LADDER].reverse(),
    order: "desc",
    collapseAfter: 4,
    expandLabel: (total: number) => `Show all ${total} levels`,
    footer: (
      <Button variant="outline" size="sm" className="self-start">
        Load more
      </Button>
    ),
  },
};
