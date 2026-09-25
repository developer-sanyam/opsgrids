import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { BellIcon } from "../icons";
import { MobileHeader } from "./MobileHeader";

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
  title: "Layout/MobileHeader",
  component: MobileHeader,
  tags: ["viewport"],
  args: {
    logo: <Mark />,
    productName: "OpsGrids Revenue",
    onOpenNav: fn(),
    search: { placeholder: "Search deals", onOpen: fn() },
    profile: (
      <span className="grid size-9 place-items-center rounded-full bg-accent-soft text-xs font-bold text-accent-ink">
        SM
      </span>
    ),
  },
} satisfies Meta<typeof MobileHeader>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** No `search` config reaches this component when the app passes none to `AppShell` — the icon
 *  disappears rather than opening nothing. */
export const WithoutSearch: Story = { args: { search: undefined } };

/** Renders whatever the app hands it — `AppShell` passes nothing at all until there is a real
 *  notification feed to show (the report's API gaps). */
export const WithNotifications: Story = {
  args: {
    notifications: (
      <button
        type="button"
        aria-label="Notifications"
        className="grid size-10 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
      >
        <BellIcon className="size-5" />
      </button>
    ),
  },
};
