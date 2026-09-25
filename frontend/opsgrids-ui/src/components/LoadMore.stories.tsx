import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { Announcer } from "./Announcer";
import { LoadMore } from "./LoadMore";

/**
 * Appends a page when asked. The `Announcer` around it is the screen's live region, which is how
 * "Loaded 25 more. Showing 50 of 130 requests." reaches a screen reader.
 */
function Paging() {
  const [loaded, setLoaded] = useState(25);
  return (
    <Announcer>
      <LoadMore loaded={loaded} total={130} noun="requests" onLoadMore={() => setLoaded((n) => Math.min(n + 25, 130))} />
    </Announcer>
  );
}

const meta = {
  title: "Navigation/LoadMore",
  component: LoadMore,
  subcomponents: { Announcer },
  args: { loaded: 25, total: 130, noun: "requests", onLoadMore: fn() },
} satisfies Meta<typeof LoadMore>;

export default meta;
type Story = StoryObj<typeof meta>;

export const More: Story = {};

export const Loading: Story = { args: { loading: true } };

/** The end of the queue is stated, so an approver knows they reached it. */
export const Exhausted: Story = { args: { loaded: 130 } };

export const Interactive: Story = { render: () => <Paging /> };
