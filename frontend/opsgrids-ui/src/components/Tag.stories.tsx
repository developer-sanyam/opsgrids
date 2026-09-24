import type { Meta, StoryObj } from "@storybook/react-vite";

import { Tag } from "./Tag";
import type { TagTone } from "./Tag";

const TONES: { tone: TagTone; text: string }[] = [
  { tone: "neutral", text: "Full-time" },
  { tone: "info", text: "Monthly" },
  { tone: "positive", text: "Verified" },
  { tone: "warning", text: "Unassigned — no approver found" },
  { tone: "negative", text: "Blocked" },
];

const meta = {
  title: "Data display/Tag",
  component: Tag,
  args: { children: "Full-time" },
} satisfies Meta<typeof Tag>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {TONES.map(({ tone, text }) => (
        <Tag key={tone} tone={tone}>
          {text}
        </Tag>
      ))}
    </div>
  ),
};
