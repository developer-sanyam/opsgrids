import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fn } from "storybook/test";

import { BoardIcon, HomeIcon, PlusIcon, SettingsIcon, TargetIcon, UsersIcon } from "../icons";
import { PageHeader } from "./PageHeader";
import { GrantsStatusBanner, ProductShell } from "./ProductShell";

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

const meta = {
  title: "Layout/ProductShell",
  component: ProductShell,
  subcomponents: { GrantsStatusBanner },
  tags: ["fullpage"],
  parameters: { layout: "fullscreen" },
  args: {
    product: { name: "OpsGrids Revenue", mark: <Mark /> },
    nav: [
      {
        title: "Pipeline",
        items: [
          { to: "/", label: "Overview", icon: HomeIcon, end: true },
          { to: "/leads", label: "Leads", icon: TargetIcon },
          { to: "/deals", label: "Deals", icon: BoardIcon },
          { to: "/contacts", label: "Contacts", icon: UsersIcon },
        ],
      },
    ],
    pinned: [{ to: "/settings", label: "Settings", icon: SettingsIcon }],
    modules: [{ key: "finance", label: "Finance", href: "https://finance.opsgrids.com", iconKey: "finance" }],
    adminLinks: [{ key: "users", label: "Users", href: "https://app.opsgrids.com/admin/members" }],
    user: { name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" },
    onSignOut: fn(),
    searchPlaceholder: "Search deals",
    searchFallback: { label: "Search deals for", to: "/deals" },
    bottomNavExtra: ["/leads", "/deals"],
    quickActions: [{ key: "new-deal", label: "New deal", icon: PlusIcon, to: "/deals" }],
  },
  render: (args) => (
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route element={<ProductShell {...args} />}>
          <Route path="*" element={<PageHeader eyebrow="Pipeline" title="Overview" />} />
        </Route>
      </Routes>
    </MemoryRouter>
  ),
} satisfies Meta<typeof ProductShell>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const PermissionsCouldNotBeChecked: Story = {
  args: {
    grants: { status: "error", reload: fn() },
    bannerCopy: {
      title: "Permissions couldn't be checked — some actions may be hidden",
      body: "Create, edit and delete controls are hidden until this succeeds. Reloading the page also retries it.",
    },
  },
};
