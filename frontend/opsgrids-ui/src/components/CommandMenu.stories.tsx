import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { BoardIcon, HomeIcon, PlusIcon, ReceiptIcon, UsersIcon } from "../icons";
import type { CommandMenuItem } from "./CommandMenu";
import { CommandMenu } from "./CommandMenu";

const ITEMS: CommandMenuItem[] = [
  { key: "nav-overview", label: "Overview", group: "Go to", icon: HomeIcon, onSelect: fn() },
  { key: "nav-deals", label: "Deals", group: "Go to", icon: BoardIcon, onSelect: fn() },
  { key: "module-hrms", label: "HRMS", group: "Modules", icon: UsersIcon, onSelect: fn() },
  { key: "module-finance", label: "Finance", group: "Modules", icon: ReceiptIcon, onSelect: fn() },
  { key: "action-new-deal", label: "New deal", group: "Quick actions", icon: PlusIcon, onSelect: fn() },
];

const meta = {
  title: "Overlays/CommandMenu",
  component: CommandMenu,
  tags: ["viewport"],
  args: { open: true, onClose: fn(), items: ITEMS, placeholder: "Search or jump to…" },
} satisfies Meta<typeof CommandMenu>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every nav item, module and quick action grouped as "Go to" / "Modules" / "Quick actions". */
export const Open: Story = {};

export const FilteredByTyping: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "fin");
    await canvas.findByText("Finance");
  },
};

export const NoMatches: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "zzz");
    await canvas.findByText("No matches for “zzz”.");
  },
};

/** Down arrow moves the highlight from the first item onward; Enter would run it and close. */
export const KeyboardNavigation: Story = {
  play: async ({ canvas, userEvent }) => {
    const input = canvas.getByRole("combobox");
    input.focus();
    await userEvent.keyboard("{ArrowDown}");
  },
};

/** Nothing above matches "acme" — the last resort: search this app's own list for it. */
export const WithFallback: Story = {
  args: { fallback: { label: "Search deals for", onSubmit: fn() } },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "acme");
    await canvas.findByText("Search deals for “acme”");
  },
};

/** Server rows for "de" listed after the matched "Deals", with a partial-results status line. */
export const WithResults: Story = {
  args: {
    results: {
      onQueryChange: fn(),
      status: "success",
      partial: true,
      items: [
        { key: "crm-company-1", label: "Delta Logistics", group: "Companies", onSelect: fn() },
        { key: "crm-lead-1", label: "Devika Rao", group: "Leads", onSelect: fn() },
      ],
    },
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.type(canvas.getByRole("combobox"), "de");
    await canvas.findByText("Some results may be missing");
  },
};
