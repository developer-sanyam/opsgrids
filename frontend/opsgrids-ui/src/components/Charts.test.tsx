/**
 * A chart is read two ways and both are pinned: as a table, by anyone with a screen reader, and
 * point by point from the keyboard, by anyone without a mouse. Plus the cards that frame them.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { UsersIcon } from "../icons";
import { BarChart } from "./BarChart";
import { BarList } from "./BarList";
import { ChartCard } from "./ChartCard";
import { DonutChart } from "./DonutChart";
import { LineChart } from "./LineChart";
import { MetricCard } from "./MetricCard";
import { EmptyState, ErrorState } from "./States";

const WEEKS = ["25 Aug", "1 Sep", "8 Sep", "15 Sep"];

function rowsOf(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole("row")
    .map((row) => Array.from(row.children).map((cell) => cell.textContent ?? ""));
}

describe("LineChart", () => {
  it("gives its data as a table, category by series", () => {
    render(
      <LineChart
        label="Cash in and out by week"
        categories={WEEKS}
        series={[
          { key: "in", label: "Inflow", values: [10, 20, 30, 40] },
          { key: "out", label: "Outflow", values: [5, 15, 10, 20], tone: "danger" },
        ]}
        formatValue={(value) => `₹${value}k`}
      />,
    );
    const table = screen.getByRole("table", { name: "Cash in and out by week" });
    expect(rowsOf(table)).toEqual([
      ["Category", "Inflow", "Outflow"],
      ["25 Aug", "₹10k", "₹5k"],
      ["1 Sep", "₹20k", "₹15k"],
      ["8 Sep", "₹30k", "₹10k"],
      ["15 Sep", "₹40k", "₹20k"],
    ]);
  });

  it("is one focusable, named plot whose arrows step the tooltip through the points", async () => {
    const user = userEvent.setup();
    render(<LineChart label="New accounts" categories={WEEKS} series={[{ key: "n", label: "New", values: [3, 5, 8, 13] }]} />);
    const plot = screen.getByRole("img", { name: "New accounts" });
    await user.tab();
    expect(document.activeElement).toBe(plot);
    // Focus shows the latest point first — the one a dashboard is usually asked about.
    expect(plot.textContent).toContain("15 Sep");
    expect(plot.textContent).toContain("13");
    await user.keyboard("{ArrowLeft}");
    expect(plot.textContent).toContain("8 Sep");
    await user.keyboard("{Home}");
    expect(plot.textContent).toContain("25 Aug");
    await user.keyboard("{Escape}");
    expect(plot.textContent).not.toContain("25 Aug");
  });
});

describe("BarChart", () => {
  it("gives its data as a table and steps through the bars from the keyboard", async () => {
    const user = userEvent.setup();
    render(
      <BarChart
        label="Tickets opened and solved"
        categories={["Mon", "Tue", "Wed"]}
        series={[
          { key: "opened", label: "Opened", values: [12, 9, 14] },
          { key: "solved", label: "Solved", values: [10, 11, 13], tone: "positive" },
        ]}
      />,
    );
    expect(rowsOf(screen.getByRole("table", { name: "Tickets opened and solved" }))[1]).toEqual(["Mon", "12", "10"]);
    await user.tab();
    await user.keyboard("{Home}");
    expect(screen.getByRole("img", { name: "Tickets opened and solved" }).textContent).toContain("Opened 12");
  });
});

describe("BarList", () => {
  it("lists every item with its value and a share that adds up to 100", () => {
    render(
      <BarList
        label="Spend by category"
        items={[
          { key: "raw", label: "Raw material", value: 600 },
          { key: "pack", label: "Packaging", value: 300 },
          { key: "freight", label: "Freight", value: 100 },
        ]}
      />,
    );
    expect(rowsOf(screen.getByRole("table", { name: "Spend by category" }))).toEqual([
      ["Item", "Value", "Share"],
      ["Raw material", "600", "60%"],
      ["Packaging", "300", "30%"],
      ["Freight", "100", "10%"],
    ]);
  });
});

describe("DonutChart", () => {
  it("names the total in its caption and gives whole-number shares", () => {
    render(
      <DonutChart
        label="Accounts by status"
        slices={[
          { key: "active", label: "Active", value: 112, tone: "positive" },
          { key: "trial", label: "Trial", value: 12, tone: "warning" },
          { key: "suspended", label: "Suspended", value: 4, tone: "danger" },
        ]}
      />,
    );
    const table = screen.getByRole("table", { name: "Accounts by status. Total: 128" });
    expect(rowsOf(table).slice(1)).toEqual([
      ["Active", "112", "88%"],
      ["Trial", "12", "9%"],
      ["Suspended", "4", "3%"],
    ]);
  });

  it("draws a smaller ring at size=sm, for a card sharing its row with two others", () => {
    const { container, rerender } = render(
      <DonutChart label="Accounts by status" slices={[{ key: "active", label: "Active", value: 1 }]} />,
    );
    const ringAt = () => container.querySelector("[aria-hidden]");
    expect(ringAt()?.className).toContain("size-36");
    rerender(
      <DonutChart label="Accounts by status" slices={[{ key: "active", label: "Active", value: 1 }]} size="sm" />,
    );
    expect(ringAt()?.className).toContain("size-24");
  });
});

describe("ChartCard", () => {
  it("is a section named by its title, with the chart in it", () => {
    render(
      <ChartCard title="Account growth" description="New accounts per week">
        <p>the chart</p>
      </ChartCard>,
    );
    const section = screen.getByRole("region", { name: "Account growth" });
    expect(within(section).getByText("the chart")).toBeTruthy();
  });

  it("shows the loading placeholder, then the error, then the empty state, in that order of precedence", () => {
    const { rerender } = render(
      <ChartCard title="Revenue" loading error={<ErrorState message="No answer." />} empty={<EmptyState title="None" description="Nothing yet." />}>
        <p>the chart</p>
      </ChartCard>,
    );
    expect(screen.getByRole("status").textContent).toBe("Loading Revenue");
    rerender(
      <ChartCard title="Revenue" error={<ErrorState message="No answer." />} empty={<EmptyState title="None" description="Nothing yet." />}>
        <p>the chart</p>
      </ChartCard>,
    );
    expect(screen.getByRole("alert").textContent).toContain("No answer.");
    rerender(
      <ChartCard title="Revenue" empty={<EmptyState title="None" description="Nothing yet." />}>
        <p>the chart</p>
      </ChartCard>,
    );
    expect(screen.getByText("Nothing yet.")).toBeTruthy();
    expect(screen.queryByText("the chart")).toBeNull();
  });
});

describe("MetricCard", () => {
  it("renders no trend at all when none is supplied", () => {
    const { container } = render(<MetricCard label="Total users" value="1,248" icon={<UsersIcon />} />);
    expect(container.textContent).toBe("Total users1,248");
  });

  it("says the direction in words, not only with an arrow and a colour", () => {
    render(<MetricCard label="Suspended" value="4" trend={{ value: -33, goodWhen: "down", period: "vs last month" }} />);
    expect(screen.getByText(/Down/).closest("p")?.textContent).toBe("Down 33%vs last month");
  });

  it("colours the arrow by whether the move is good for this metric", () => {
    const { container, rerender } = render(<MetricCard label="Revenue" value="₹12L" trend={{ value: 14 }} />);
    expect(container.querySelector("svg")?.getAttribute("class")).toContain("text-positive");
    rerender(<MetricCard label="Open incidents" value="9" trend={{ value: 14, goodWhen: "down" }} />);
    expect(container.querySelector("svg")?.getAttribute("class")).toContain("text-negative");
  });
});
