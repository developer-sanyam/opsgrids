import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fn } from "storybook/test";

import {
  BoardIcon,
  BriefcaseIcon,
  HomeIcon,
  PlusIcon,
  ReceiptIcon,
  SettingsIcon,
  ShieldIcon,
  UsersIcon,
} from "../icons";
import type { AppShellProps } from "./AppShell";
import { AppShell } from "./AppShell";
import { BottomNavigation } from "./BottomNavigation";
import { Button } from "./Button";
import { CommandMenu } from "./CommandMenu";
import { KpiCard } from "./KpiCard";
import { MobileHeader } from "./MobileHeader";
import { PageHeader } from "./PageHeader";
import { Sidebar } from "./Sidebar";
import { SectionHeader } from "./States";

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

function Screen() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Overview"
        actions={<Button leadingIcon={<PlusIcon className="size-5" />}>New deal</Button>}
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <KpiCard label="Open pipeline" value="₹70.38L" deltaPct={12.4} icon={<BriefcaseIcon className="size-5" />} tone="accent" />
        <KpiCard label="Deals in play" value="48" icon={<BoardIcon className="size-5" />} tone="info" />
        <KpiCard label="New contacts" value="36" deltaPct={-3.2} icon={<UsersIcon className="size-5" />} tone="cyan" />
      </div>
      <div className="rounded-card border border-line bg-surface p-5 shadow-card">
        <SectionHeader title="Needs attention" subtitle="Deals with no activity in 14 days" />
        <p className="text-sm text-muted">Nothing is stalled. Every open deal has moved this fortnight.</p>
      </div>
    </div>
  );
}

const meta = {
  title: "Layout/AppShell",
  component: AppShell,
  subcomponents: { Sidebar, MobileHeader, BottomNavigation, CommandMenu },
  // A page's frame: photographed as the whole document, below the fold included.
  tags: ["fullpage"],
  parameters: { layout: "fullscreen" },
  args: {
    product: { name: "OpsGrids Revenue", mark: <Mark /> },
    nav: [
      {
        title: "Pipeline",
        items: [
          { to: "/", label: "Overview", icon: HomeIcon, end: true },
          { to: "/deals", label: "Deals", icon: BoardIcon, count: "48" },
          { to: "/contacts", label: "Contacts", icon: UsersIcon },
        ],
      },
    ],
    pinned: [{ to: "/settings", label: "Settings", icon: SettingsIcon }],
    modules: [
      { key: "hrms", label: "HRMS", href: "https://people.opsgrids.com", icon: UsersIcon },
      { key: "finance", label: "Finance", href: "https://finance.opsgrids.com", icon: ReceiptIcon },
    ],
    adminLinks: [
      { key: "users", label: "Users", href: "/admin/members", icon: UsersIcon },
      { key: "audit-logs", label: "Audit Logs", href: "/admin/audit", icon: ShieldIcon },
    ],
    user: { name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" },
    onSignOut: fn(),
    search: { placeholder: "Search deals" },
    quickActions: [{ key: "new-deal", label: "New deal", icon: PlusIcon, onSelect: fn() }],
  } satisfies Partial<AppShellProps>,
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell {...(args as AppShellProps)} />}>
          <Route path="*" element={<Screen />} />
        </Route>
      </Routes>
    </MemoryRouter>
  ),
} satisfies Meta<typeof AppShell>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A persistent sidebar from 1280px, an icon rail from 768, and the compact header below that. */
export const Default: Story = {};

/** The narrow-screen nav panel, opened — the same content the tablet rail's expand button and the
 *  bottom nav's More button also reach. */
export const NavigationDrawerOpen: Story = {
  tags: ["!fullpage", "viewport"],
  play: async ({ canvas, userEvent }) => {
    const open = canvas.queryByRole("button", { name: "Open navigation" });
    if (open) {
      await userEvent.click(open);
      await canvas.findByRole("dialog", { name: "Navigation" });
    }
  },
};

/** ⌘K, or the header's search trigger: every nav item, module and quick action in one list. */
export const CommandMenuOpen: Story = {
  tags: ["!fullpage", "viewport"],
  play: async ({ canvas, userEvent }) => {
    const trigger = canvas.getAllByRole("button", { name: /search deals/i })[0];
    if (trigger) {
      await userEvent.click(trigger);
      await canvas.findByRole("dialog", { name: "Command menu" });
    }
  },
};

/** No `modules`, no `quickActions`: the staff console's shape — the bottom nav loses those slots
 *  rather than showing them empty, and the sidebar's Modules and Admin groups don't render. */
export const StaffConsole: Story = {
  args: {
    modules: undefined,
    adminLinks: undefined,
    quickActions: undefined,
    search: undefined,
    user: { name: "Jordan Blake", email: "jordan@opsgrids.com", roleLabel: "Owner" },
  },
};
