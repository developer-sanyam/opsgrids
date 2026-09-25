import type { Meta, StoryObj } from "@storybook/react-vite";

import * as icons from "../icons";

/** Every export of `@opsgrids/ui/icons`, read from the module — a new icon appears here unasked. */
const ALL = Object.entries(icons).sort(([a], [b]) => a.localeCompare(b));

function Gallery({ size }: { size: "size-4" | "size-5" | "size-6" }) {
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
      {ALL.map(([name, Icon]) => (
        <li key={name} className="flex items-center gap-2 rounded-tile bg-surface px-3 py-2 text-ink-soft">
          <Icon className={`${size} shrink-0`} />
          <span className="truncate text-xs">{name}</span>
        </li>
      ))}
    </ul>
  );
}

const meta = {
  title: "Foundations/Icons",
  component: Gallery,
  args: { size: "size-5" },
} satisfies Meta<typeof Gallery>;

export default meta;
type Story = StoryObj<typeof meta>;

export const All: Story = {};
