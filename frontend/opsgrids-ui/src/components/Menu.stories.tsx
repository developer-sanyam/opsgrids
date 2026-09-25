import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, fn, waitFor } from "storybook/test";

import { DownloadIcon, FileTextIcon, SettingsIcon } from "../icons";
import { Menu } from "./Menu";
import type { MenuItem } from "./Menu";

const EXPORT: MenuItem[] = [
  { label: "Export CSV", onSelect: fn(), icon: <DownloadIcon className="size-4" /> },
  { label: "Export PDF", onSelect: fn(), icon: <FileTextIcon className="size-4" /> },
  { label: "Schedule a report", onSelect: fn() },
];

const ACCOUNT: MenuItem[] = [
  { label: "View account", onSelect: fn() },
  { label: "Change plan", onSelect: fn() },
  { label: "Billing", onSelect: fn() },
  { label: "Impersonate", onSelect: fn(), disabled: true, disabledReason: "Your role can't access customer accounts." },
  { label: "Suspend account", onSelect: fn(), tone: "danger" },
];

const meta = {
  title: "Actions/Menu",
  component: Menu,
  args: { label: "Export", items: EXPORT },
  render: (args) => (
    <div className="flex min-h-72 items-start justify-between gap-4">
      <Menu {...args} />
    </div>
  ),
} satisfies Meta<typeof Menu>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Closed: Story = {};

export const WithIcon: Story = { args: { icon: <SettingsIcon className="size-4" />, label: "Workspace" } };

export const Open: Story = {
  tags: ["viewport"],
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "Export" }));
    const menu = await canvas.findByRole("menu");
    await waitFor(() => expect(menu.contains(document.activeElement)).toBe(true));
  },
};

/** The ⋯ trigger at a card's right edge; the menu lines up with it. A disabled item reads its reason. */
export const IconTriggerAlignedEnd: Story = {
  tags: ["viewport"],
  args: { label: "Actions for Acme Industries", items: ACCOUNT, trigger: "icon", align: "end" },
  render: (args) => (
    <div className="flex min-h-80 justify-end">
      <Menu {...args} />
    </div>
  ),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "Actions for Acme Industries" }));
    const menu = await canvas.findByRole("menu");
    await waitFor(() => expect(menu.contains(document.activeElement)).toBe(true));
  },
};

/** Opened with the keyboard and moved with typeahead: "c" lands on Change plan. */
export const KeyboardTypeahead: Story = {
  tags: ["viewport"],
  args: { label: "Account actions", items: ACCOUNT },
  play: async ({ canvas, userEvent }) => {
    await userEvent.tab();
    await userEvent.keyboard("{ArrowDown}");
    const menu = await canvas.findByRole("menu");
    // Typeahead listens on the menu, so the letter waits until focus has moved into it.
    await waitFor(() => expect(menu.contains(document.activeElement)).toBe(true));
    await userEvent.keyboard("c");
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Change plan"));
  },
};
