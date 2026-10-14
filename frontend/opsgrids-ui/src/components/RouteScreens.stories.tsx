import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router-dom";

import { BuildingIcon } from "../icons";
import { ComingSoonPage, NotFoundScreen, PermissionDenied, RouteFallback } from "./RouteScreens";

const meta = {
  title: "Feedback/RouteScreens",
  component: NotFoundScreen,
  subcomponents: { ComingSoonPage, PermissionDenied, RouteFallback },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-3xl">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof NotFoundScreen>;

export default meta;
type Story = StoryObj<typeof meta>;

export const NotFound: Story = {};

export const NotFoundWithOwnWayBack: Story = {
  args: { description: "The page you were looking for isn't here. Head back to your tasks.", backLabel: "Back to tasks" },
};

export const ComingSoon: Story = {
  render: () => (
    <ComingSoonPage
      icon={<BuildingIcon className="size-6" />}
      title="Departments"
      description="Org units and headcount arrive in a later release."
    />
  ),
};

export const PermissionDeniedScreen: Story = {
  render: () => <PermissionDenied what="the audit log" />,
};

export const Loading: Story = {
  render: () => <RouteFallback />,
};
