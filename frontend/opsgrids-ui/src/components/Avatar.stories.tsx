import type { Meta, StoryObj } from "@storybook/react-vite";

import { Avatar } from "./Avatar";
import type { AvatarSize } from "./Avatar";

const SIZES: AvatarSize[] = ["xs", "sm", "md", "lg"];

/** Names chosen to land on each of the six tints (the tint is a hash of the name). */
const PEOPLE = [
  "Sofia Marchetti",
  "Arjun Mehta",
  "Priya Raman",
  "Kwame Mensah",
  "Lena Fischer",
  "Tomás Álvarez",
  "Mei Lin",
  "Oluwaseun Adeyemi-Carter",
];

const meta = {
  title: "Data display/Avatar",
  component: Avatar,
  args: { name: "Sofia Marchetti" },
} satisfies Meta<typeof Avatar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Sizes: Story = {
  render: (args) => (
    <div className="flex items-end gap-3">
      {SIZES.map((size) => (
        <Avatar key={size} {...args} size={size} />
      ))}
    </div>
  ),
};

/** The tint is deterministic per name, so a person keeps their colour everywhere. */
export const Tints: Story = {
  render: () => (
    <div className="flex flex-wrap gap-3">
      {PEOPLE.map((name) => (
        <Avatar key={name} name={name} />
      ))}
    </div>
  ),
};

/** A single name gives one initial; an empty-ish name must not break the circle. */
export const EdgeCases: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Avatar name="Madonna" />
      <Avatar name="  " />
      <Avatar name="ज्योति शर्मा" />
    </div>
  ),
};
