import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { fn } from "storybook/test";

import { Announcer } from "./Announcer";
import { KanbanBoard } from "./KanbanBoard";
import type { KanbanBoardProps, KanbanColumn } from "./KanbanBoard";

interface Deal {
  id: string;
  name: string;
  company: string;
  stage: string;
}

const COLUMNS: KanbanColumn[] = [
  { id: "qualify", label: "Qualify", tone: "neutral", summary: "₹4.2L" },
  { id: "proposal", label: "Proposal", tone: "info", summary: "₹12L + $8K" },
  { id: "won", label: "Won", tone: "success", closed: true },
  { id: "lost", label: "Lost", tone: "danger", closed: true, emptyLabel: "No lost deals this quarter" },
];

const DEALS: Deal[] = [
  { id: "d1", name: "Fleet telematics rollout", company: "Vaayu Foods", stage: "qualify" },
  { id: "d2", name: "Warehouse sensors", company: "Northwind Trading", stage: "qualify" },
  { id: "d3", name: "Annual support renewal", company: "Globex India", stage: "proposal" },
  { id: "d4", name: "Payroll migration", company: "Initech", stage: "won" },
];

type BoardArgs = Partial<KanbanBoardProps<Deal>> & { initial?: Deal[] };

/** The consumer every app writes: it owns the items and applies the move. */
function DealBoard({ initial = DEALS, onMove, ...props }: BoardArgs) {
  const [items, setItems] = useState(initial);
  return (
    <Announcer>
      <KanbanBoard<Deal>
        columns={COLUMNS}
        items={items}
        getItemId={(deal) => deal.id}
        getColumnId={(deal) => deal.stage}
        renderCard={(deal, state) => (
          <>
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-semibold text-ink">{deal.name}</p>
              {state.actions}
            </div>
            <p className="text-xs text-muted">{deal.company}</p>
          </>
        )}
        canMove={(deal, to) => deal.stage !== "won" && to !== "lost"}
        moveMenuLabel={(deal) => deal.name}
        announce={(deal, _from, to, result) =>
          result === "moving" ? `Moving ${deal.name} to ${to}…` : result === "moved" ? `${deal.name} moved to ${to}.` : `${deal.name} could not move.`
        }
        {...props}
        onMove={async (deal, to) => {
          setItems((previous) => previous.map((d) => (d.id === deal.id ? { ...d, stage: to } : d)));
          await onMove?.(deal, to);
        }}
      />
    </Announcer>
  );
}

const meta = {
  title: "Data display/KanbanBoard",
  component: KanbanBoard,
  render: (args) => <DealBoard {...(args as BoardArgs)} />,
  args: {} as KanbanBoardProps<unknown>,
} satisfies Meta<typeof KanbanBoard>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every card's moves are in its menu; a refused move ("Move to Lost") is listed, disabled, with why. */
export const Default: Story = {};

export const EmptyColumns: Story = {
  render: () => <DealBoard initial={[]} />,
};

/** Closed columns refuse a drop; a won deal offers its moves disabled, each with a reason. */
export const ClosedColumns: Story = {
  render: () => <DealBoard initial={DEALS.filter((deal) => deal.stage === "won")} />,
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "More actions for Payroll migration" }));
  },
};

/** A stage the board was not given is listed under "Other", not dropped. */
export const UnknownColumn: Story = {
  render: () => <DealBoard initial={[...DEALS, { id: "d5", name: "Kiosk pilot", company: "Umbrella Retail", stage: "on_hold" }]} />,
};

export const ShowMore: Story = {
  render: () => (
    <DealBoard
      columns={[{ ...COLUMNS[0], id: "qualify", label: "Qualify", total: 40 }, ...COLUMNS.slice(1)]}
      onLoadMore={fn()}
      loadingColumnIds={new Set<string>()}
    />
  ),
};

/** A move under way: the card is dimmed and busy until the consumer's write settles. */
export const Moving: Story = {
  render: () => <DealBoard onMove={() => new Promise<void>(() => undefined)} />,
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole("button", { name: "More actions for Fleet telematics rollout" }));
    await userEvent.click(canvas.getByRole("menuitem", { name: "Move to Proposal" }));
  },
};

export const ReadOnly: Story = {
  render: () => <DealBoard readOnly />,
};

export const PhoneWidth: Story = {
  render: () => (
    <div className="max-w-sm">
      <DealBoard />
    </div>
  ),
};
