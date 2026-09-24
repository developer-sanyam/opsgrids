import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fn } from "storybook/test";

import { BoardIcon, BriefcaseIcon, HomeIcon, PlusIcon, SettingsIcon, UsersIcon } from "../icons";
import { AppShell } from "./AppShell";
import { Button } from "./Button";
import { Card } from "./Card";
import { KpiCard } from "./KpiCard";
import { PageHeader } from "./PageHeader";
import { Sidebar } from "./Sidebar";
import { SectionHeader } from "./States";

function Mark() {
  return (
    <span className="grid size-7 shrink-0 place-items-center rounded-tile bg-accent text-xs font-bold text-white" aria-hidden>
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
      <Card className="flex flex-col gap-3 p-5">
        <SectionHeader title="Needs attention" subtitle="Deals with no activity in 14 days" />
        <p className="text-sm text-muted">Nothing is stalled. Every open deal has moved this fortnight.</p>
      </Card>
    </div>
  );
}

const meta = {
  title: "Layout/AppShell",
  component: AppShell,
  subcomponents: { Sidebar },
  // A page's frame: photographed as the whole document, below the fold included.
  tags: ["fullpage"],
  parameters: { layout: "fullscreen" },
  args: {
    logo: <Mark />,
    productName: "OpsGrids Revenue",
    renderSidebar: ({ onNavigate }) => (
      <Sidebar
        groups={[
          {
            title: "Pipeline",
            items: [
              { to: "/", label: "Overview", icon: HomeIcon, end: true },
              { to: "/deals", label: "Deals", icon: BoardIcon, count: "48" },
              { to: "/contacts", label: "Contacts", icon: UsersIcon },
            ],
          },
        ]}
        pinned={[{ to: "/settings", label: "Settings", icon: SettingsIcon }]}
        account={{ name: "Sofia Marchetti", email: "sofia@acme.example", organizationName: "Acme Industries" }}
        onSignOut={fn()}
        logo={<Mark />}
        product={{ name: "OpsGrids Revenue", tagline: "Sales workspace" }}
        search={{ label: "Search deals", placeholder: "Search deals", to: "/deals" }}
        {...(onNavigate ? { onNavigate } : {})}
      />
    ),
  },
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<AppShell {...args} />}>
          <Route path="*" element={<Screen />} />
        </Route>
      </Routes>
    </MemoryRouter>
  ),
} satisfies Meta<typeof AppShell>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A persistent sidebar from 1024px; the compact bar and a drawer below it. */
export const Default: Story = {};

/** The narrow-screen drawer, opened. At desktop width there is no drawer to open. */
export const DrawerOpen: Story = {
  // The drawer is an overlay: what a phone shows is the viewport, not the page behind it.
  tags: ["!fullpage", "viewport"],
  play: async ({ canvas, userEvent }) => {
    const open = canvas.queryByRole("button", { name: "Open navigation" });
    if (open) {
      await userEvent.click(open);
      await canvas.findByRole("dialog", { name: "Navigation" });
    }
  },
};
