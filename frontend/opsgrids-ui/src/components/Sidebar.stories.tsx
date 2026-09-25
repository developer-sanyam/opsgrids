import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router-dom";

import {
  BoardIcon,
  BriefcaseIcon,
  HomeIcon,
  ReceiptIcon,
  SettingsIcon,
  ShieldIcon,
  UsersIcon,
} from "../icons";
import type { ModuleLink, NavGroup } from "./Sidebar";
import { Sidebar } from "./Sidebar";

function Mark() {
  return (
    <span
      className="grid size-7 shrink-0 place-items-center rounded-tile bg-accent text-xs font-bold text-on-accent"
      aria-hidden
    >
      R
    </span>
  );
}

const GROUPS: NavGroup[] = [
  {
    title: "Pipeline",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/deals", label: "Deals", icon: BoardIcon, count: "48" },
      { to: "/contacts", label: "Contacts", icon: UsersIcon },
    ],
  },
];

const MODULES: ModuleLink[] = [
  { key: "hrms", label: "HRMS", href: "https://people.opsgrids.com", icon: UsersIcon },
  { key: "finance", label: "Finance", href: "https://finance.opsgrids.com", icon: ReceiptIcon },
];

const ADMIN: ModuleLink[] = [
  { key: "users", label: "Users", href: "/admin/members", icon: UsersIcon },
  { key: "audit-logs", label: "Audit Logs", href: "/admin/audit", icon: ShieldIcon },
];

const meta = {
  title: "Layout/Sidebar",
  component: Sidebar,
  args: {
    groups: GROUPS,
    pinned: [{ to: "/settings", label: "Settings", icon: SettingsIcon }],
    user: { name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" },
    logo: <Mark />,
    productName: "OpsGrids Revenue",
  },
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <div className="shell-side-h shell-sidebar-w">
        <Sidebar {...args} />
      </div>
    </MemoryRouter>
  ),
} satisfies Meta<typeof Sidebar>;

export default meta;
type Story = StoryObj<typeof meta>;

/** This app's own nav and pinned Settings. No cross-product modules or admin links passed. */
export const Default: Story = {};

/** Modules and Admin groups appear only once the app supplies them — never empty headings. */
export const WithModulesAndAdmin: Story = {
  args: { modules: MODULES, adminLinks: ADMIN },
};

/** The 72px tablet rail: labels move off-screen (still read by a screen reader) and a native
 *  tooltip names each icon on hover. */
export const Collapsed: Story = {
  args: { modules: MODULES, adminLinks: ADMIN, collapsed: true },
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <div className="shell-side-h shell-rail-w">
        <Sidebar {...args} />
      </div>
    </MemoryRouter>
  ),
};

/** No tenant modules — the staff console's shape: this app's own nav and identity only. */
export const StaffConsole: Story = {
  args: {
    groups: [
      {
        title: "Console",
        items: [
          { to: "/", label: "Overview", icon: HomeIcon, end: true },
          { to: "/accounts", label: "Accounts", icon: BriefcaseIcon },
        ],
      },
    ],
    pinned: undefined,
    modules: undefined,
    adminLinks: undefined,
    user: { name: "Jordan Blake", email: "jordan@opsgrids.com", roleLabel: "Owner" },
  },
};
