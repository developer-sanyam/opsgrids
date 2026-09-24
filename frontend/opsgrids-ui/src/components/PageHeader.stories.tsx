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
