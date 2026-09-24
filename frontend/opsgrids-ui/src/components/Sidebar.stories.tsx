import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router-dom";
import { fn } from "storybook/test";

import {
  ActivityIcon,
  BarChartIcon,
  BoardIcon,
  BuildingIcon,
  FunnelIcon,
  HomeIcon,
  LifeBuoyIcon,
  SettingsIcon,
  UsersIcon,
} from "../icons";
import { Sidebar } from "./Sidebar";
import type { NavGroup } from "./Sidebar";

const GROUPS: NavGroup[] = [
  {
    title: "Pipeline",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/leads", label: "Leads", icon: FunnelIcon, badge: "12" },
      { to: "/deals", label: "Deals", icon: BoardIcon, count: "48" },
      { to: "/activities", label: "Activities", icon: ActivityIcon },
    ],
  },
  {
    title: "Records",
    items: [
      { to: "/companies", label: "Companies", icon: BuildingIcon, count: "126" },
      { to: "/contacts", label: "Contacts", icon: UsersIcon, count: "1,204" },
      { to: "/reports", label: "Reports", icon: BarChartIcon },
    ],
  },
];

function Mark() {
  return (
    <span className="grid size-8 shrink-0 place-items-center rounded-tile bg-accent text-sm font-bold text-white" aria-hidden>
      R
    </span>
  );
}

const meta = {
  title: "Navigation/Sidebar",
  component: Sidebar,
  args: {
    groups: GROUPS,
    pinned: [
      { to: "/settings", label: "Settings", icon: SettingsIcon },
      { to: "/help", label: "Help", icon: LifeBuoyIcon },
    ],
    account: { name: "Sofia Marchetti", email: "sofia@acme.example", organizationName: "Acme Industries" },
    onSignOut: fn(),
    logo: <Mark />,
    product: { name: "OpsGrids Revenue", tagline: "Sales workspace" },
    search: { label: "Search deals", placeholder: "Search deals", to: "/deals" },
  },
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={["/deals"]}>
        <div className="h-180 w-62">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof Sidebar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** For a count that is a problem (overdue invoices) rather than merely new. */
export const DangerBadge: Story = { args: { badgeTone: "danger" } };

export const NoOrganization: Story = {
  args: { account: { name: "Oluwaseun Adeyemi-Carter", email: "oluwaseun.adeyemi-carter@a-very-long-domain.example" } },
};

export const AccountMenuOpen: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: /Sofia Marchetti/ }));
    await canvas.findByRole("menuitem", { name: "Sign out" });
  },
};
