import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router-dom";
import { fn } from "storybook/test";

import { FileTextIcon, GridIcon, HomeIcon, MoreHorizontalIcon, PlusIcon } from "../icons";
import type { BottomNavItem } from "./BottomNavigation";
import { BottomNavigation } from "./BottomNavigation";

const FULL: BottomNavItem[] = [
  { key: "home", label: "Home", icon: HomeIcon, to: "/" },
  { key: "modules", label: "Modules", icon: GridIcon, onSelect: fn() },
  { key: "quick-actions", label: "New", icon: PlusIcon, onSelect: fn(), raised: true },
  { key: "reports", label: "Reports", icon: FileTextIcon, to: "/reports" },
  { key: "more", label: "More", icon: MoreHorizontalIcon, onSelect: fn() },
];

const meta = {
  title: "Layout/BottomNavigation",
  component: BottomNavigation,
  tags: ["viewport"],
  args: { items: FULL },
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <div className="relative h-32">
        <BottomNavigation {...args} />
      </div>
    </MemoryRouter>
  ),
} satisfies Meta<typeof BottomNavigation>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Home, Modules, the raised "+", an app-defined fourth slot and More. */
export const Full: Story = {};

/** No Modules (no tenant modules — the staff console) and no fourth slot: the bar simply has
 *  fewer, evenly spaced destinations rather than an empty gap. */
export const Minimal: Story = {
  args: {
    items: [
      { key: "home", label: "Home", icon: HomeIcon, to: "/" },
      { key: "more", label: "More", icon: MoreHorizontalIcon, onSelect: fn() },
    ],
  },
};
