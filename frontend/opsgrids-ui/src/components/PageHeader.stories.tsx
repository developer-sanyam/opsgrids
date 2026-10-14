import type { Meta, StoryObj } from "@storybook/react-vite";

import { DownloadIcon, PlusIcon } from "../icons";
import { Button } from "./Button";
import { PageHeader } from "./PageHeader";

const meta = {
  title: "Layout/PageHeader",
  component: PageHeader,
  args: { title: "Deals" },
} satisfies Meta<typeof PageHeader>;

export default meta;
type Story = StoryObj<typeof meta>;

export const TitleOnly: Story = {};

export const Full: Story = {
  args: {
    eyebrow: "Pipeline",
    title: "Deals",
    description: "Every open opportunity in this organization, by stage. Drag a card to move it.",
    actions: (
      <>
        <Button variant="outline" leadingIcon={<DownloadIcon className="size-5" />}>
          Export
        </Button>
        <Button leadingIcon={<PlusIcon className="size-5" />}>New deal</Button>
      </>
    ),
  },
};

/** Long titles balance across lines; the actions wrap under them on a phone. */
export const LongTitle: Story = {
  args: {
    eyebrow: "Accounts › Consolidated Northern & Southern Agricultural Cooperative Federation",
    title: "Consolidated Northern & Southern Agricultural Cooperative Federation Private Limited",
    actions: <Button>Edit account</Button>,
  },
};

/** A dashboard whose title and period control must share one row on a phone (mock-1, mock-2):
 *  the description drops below `sm` instead of pushing the actions onto a line of their own.
 *  Resize the canvas below 640px to see it disappear. */
export const DescriptionHiddenOnPhone: Story = {
  args: {
    title: (
      <>
        <span className="sm:hidden">Super Admin</span>
        <span className="hidden sm:inline">Super Admin Dashboard</span>
      </>
    ),
    description: "Manage all accounts, monitor usage, and oversee the platform.",
    descriptionClassName: "hidden sm:block",
    actions: <Button variant="outline">Last 30 Days</Button>,
  },
};

/** A period picker beside the page's primary action. The "Showing ..." line is `meta`, under the
 *  title, so the select and the button share one top and one bottom edge. */
export const WithMetaAndPeriodControl: Story = {
  args: {
    eyebrow: "Communication",
    title: "Email",
    description: "Manage email accounts, templates, automations and track performance.",
    meta: <span role="status">Showing 15 Sep 2026 – 14 Oct 2026</span>,
    actions: (
      <>
        <Button variant="secondary">Last 30 days</Button>
        <Button leadingIcon={<PlusIcon className="size-5" />}>Create campaign</Button>
      </>
    ),
  },
};
