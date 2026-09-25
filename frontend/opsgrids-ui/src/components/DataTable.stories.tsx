import { useMemo, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";

import { FilterIcon, InboxIcon, PlusIcon } from "../icons";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { Card } from "./Card";
import { DataTable } from "./DataTable";
import type { Column } from "./DataTable";
import { MobileDataCard } from "./MobileDataCard";
import { Pill } from "./Pill";
import { RowActions } from "./RowActions";
import { SearchInput } from "./SearchInput";
import { EmptyState, ErrorState } from "./States";
import { Tag } from "./Tag";

interface Deal {
  id: string;
  name: string;
  company: string;
  owner: string;
  stage: "Qualified" | "Proposal" | "Negotiation" | "Won";
  value: number;
  closes: string;
}

const DEALS: Deal[] = [
  { id: "d-1", name: "Warehouse automation — phase 2", company: "Acme Industries", owner: "Arjun Mehta", stage: "Negotiation", value: 1840000, closes: "30 Sep 2026" },
  { id: "d-2", name: "Annual support renewal", company: "Bluefin Logistics", owner: "Priya Raman", stage: "Proposal", value: 210000, closes: "12 Oct 2026" },
  { id: "d-3", name: "Fleet telematics pilot", company: "Kaveri Transport", owner: "Sofia Marchetti", stage: "Qualified", value: 560000, closes: "4 Nov 2026" },
  { id: "d-4", name: "Cold-chain sensors, 40 sites", company: "Northwind Foods", owner: "Arjun Mehta", stage: "Won", value: 2975000, closes: "2 Sep 2026" },
  { id: "d-5", name: "ERP migration advisory", company: "Saffron Retail", owner: "Kwame Mensah", stage: "Proposal", value: 725000, closes: "18 Oct 2026" },
];

const STAGE_TONE = {
  Qualified: "info",
  Proposal: "neutral",
  Negotiation: "warning",
  Won: "positive",
} as const;

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

const COLUMNS: Column<Deal>[] = [
  {
    key: "name",
    header: "Deal",
    sortKey: "name",
    render: (deal) => (
      <div className="flex flex-col">
        <span className="font-semibold text-ink">{deal.name}</span>
        <span className="text-xs text-muted">{deal.company}</span>
      </div>
    ),
  },
  {
    key: "owner",
    header: "Owner",
    cellClassName: "hidden md:table-cell",
    headerClassName: "hidden md:table-cell",
    render: (deal) => (
      <span className="flex items-center gap-2">
        <Avatar name={deal.owner} size="xs" />
        {deal.owner}
      </span>
    ),
  },
  { key: "stage", header: "Stage", render: (deal) => <Tag tone={STAGE_TONE[deal.stage]}>{deal.stage}</Tag> },
  {
    key: "value",
    header: "Value",
    align: "right",
    sortKey: "value",
    cellClassName: "tnum",
    render: (deal) => inr.format(deal.value),
  },
  {
    key: "closes",
    header: "Closes",
    cellClassName: "hidden lg:table-cell text-muted",
    headerClassName: "hidden lg:table-cell",
    render: (deal) => deal.closes,
  },
];

const ACTIONS_COLUMN: Column<Deal> = {
  key: "actions",
  header: <span className="sr-only">Actions</span>,
  align: "right",
  render: (deal) => (
    <RowActions
      label={deal.name}
      actions={[
        { label: "Edit", onSelect: fn() },
        { label: "Move stage", onSelect: fn() },
        { label: "Delete", onSelect: fn(), tone: "danger" },
      ]}
      inlineLimit={1}
    />
  ),
};

function Sortable() {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "value", dir: "desc" });
  const rows = useMemo(() => {
    const sorted = [...DEALS].sort((a, b) =>
      sort.key === "name" ? a.name.localeCompare(b.name) : a.value - b.value,
    );
    return sort.dir === "asc" ? sorted : sorted.reverse();
  }, [sort]);
  return (
    <DataTable
      caption="Open deals"
      columns={COLUMNS}
      rows={rows}
      rowKey={(deal) => deal.id}
      sort={sort}
      onSort={(key) =>
        setSort((previous) => ({
          key,
          dir: previous.key === key && previous.dir === "asc" ? "desc" : "asc",
        }))
      }
    />
  );
}

function Selectable() {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set(["d-2", "d-5"]));
  return (
    <DataTable
      caption="Deals"
      columns={COLUMNS}
      rows={DEALS}
      rowKey={(deal) => deal.id}
      selection={{
        selected,
        label: (deal) => deal.name,
        onToggle: (key, next) =>
          setSelected((previous) => {
            const copy = new Set(previous);
            if (next) copy.add(key);
            else copy.delete(key);
            return copy;
          }),
        onToggleAll: (next) => setSelected(next ? new Set(DEALS.map((deal) => deal.id)) : new Set()),
      }}
      toolbar={
        <div className="flex items-center justify-between gap-3 rounded-tile bg-accent-soft px-4 py-2">
          <span className="text-sm font-semibold text-accent-ink">{selected.size} selected</span>
          <Button size="sm" variant="danger">
            Delete selected
          </Button>
        </div>
      }
    />
  );
}

const TOOLBAR = (
  <div className="flex flex-wrap items-center gap-2">
    <SearchInput label="Search deals" placeholder="Search deals" containerClassName="w-full sm:w-72" />
    <Pill active>All</Pill>
    <Pill>Mine</Pill>
    <Pill leadingIcon={<FilterIcon className="size-4" />}>Closing this month</Pill>
  </div>
);

const meta = {
  title: "Data display/DataTable",
  component: DataTable<Deal>,
  args: {
    caption: "Open deals",
    columns: COLUMNS,
    rows: DEALS,
    rowKey: (deal: Deal) => deal.id,
  },
} satisfies Meta<typeof DataTable<Deal>>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const WithToolbarAndActions: Story = {
  args: { toolbar: TOOLBAR, columns: [...COLUMNS, ACTIONS_COLUMN] },
};

export const SortedByValue: Story = { render: () => <Sortable /> };

/** Rows are one control each (pointer and keyboard); the current row is marked. */
export const ClickableRows: Story = {
  args: { onRowClick: fn(), isSelected: (deal: Deal) => deal.id === "d-3" },
};

export const BulkSelection: Story = { render: () => <Selectable /> };

/** A filter that matched nothing keeps its toolbar, so the user can undo it. */
export const Empty: Story = {
  args: {
    rows: [],
    toolbar: TOOLBAR,
    emptyState: (
      <EmptyState
        icon={<InboxIcon className="size-6" />}
        title="No deals match these filters"
        description="Clear the search or pick another stage to see more of the pipeline."
        action={
          <Button size="sm" leadingIcon={<PlusIcon className="size-4" />}>
            New deal
          </Button>
        }
      />
    ),
  },
};

/* ---- v2: loading, failure, bulk actions, a pinned header, cards on a phone ------------------ */

interface Account {
  id: string;
  company: string;
  domain: string;
  plan: string;
  status: "Active" | "Trial" | "Suspended";
  users: number;
  revenue: number;
  created: string;
}

const ACCOUNTS: Account[] = [
  { id: "a-1", company: "Acme Pvt Ltd", domain: "acme.in", plan: "Enterprise", status: "Active", users: 24, revenue: 49800, created: "12 Aug 2026" },
  { id: "a-2", company: "Global Traders", domain: "globaltraders.com", plan: "Pro", status: "Active", users: 18, revenue: 24800, created: "5 Aug 2026" },
  { id: "a-3", company: "Shree Distributors", domain: "shreedist.com", plan: "Business", status: "Trial", users: 8, revenue: 0, created: "20 Sep 2026" },
  { id: "a-4", company: "Rohit Enterprises", domain: "rohitent.in", plan: "Pro", status: "Active", users: 12, revenue: 24800, created: "2 Aug 2026" },
  { id: "a-5", company: "NAC Brands", domain: "nacbrands.com", plan: "Business", status: "Suspended", users: 10, revenue: 0, created: "10 Jul 2026" },
];

const ACCOUNT_COLUMNS: Column<Account>[] = [
  {
    key: "company",
    header: "Company",
    render: (account) => (
      <div className="flex flex-col">
        <span className="font-semibold text-ink">{account.company}</span>
        <span className="text-xs text-muted">{account.domain}</span>
      </div>
    ),
  },
  { key: "plan", header: "Plan", render: (account) => <Tag tone="info">{account.plan}</Tag>, mobile: "status" },
  { key: "status", header: "Status", render: (account) => <Tag>{account.status}</Tag>, mobile: "status" },
  { key: "users", header: "Users", align: "right", cellClassName: "tnum", render: (account) => account.users },
  { key: "revenue", header: "Revenue", align: "right", cellClassName: "tnum", render: (account) => inr.format(account.revenue) },
  {
    key: "created",
    header: "Created",
    cellClassName: "hidden lg:table-cell text-muted",
    headerClassName: "hidden lg:table-cell",
    render: (account) => account.created,
    mobile: "hidden",
  },
  {
    key: "actions",
    header: <span className="sr-only">Actions</span>,
    align: "right",
    mobile: "actions",
    render: (account) => (
      <RowActions
        label={account.company}
        inlineLimit={2}
        actions={[
          { label: "View", onSelect: fn() },
          { label: "Change plan", onSelect: fn() },
        ]}
      />
    ),
  },
];

/** The same columns with nothing operable in the cells — for a table whose rows are links. */
const ACCOUNT_COLUMNS_PLAIN = ACCOUNT_COLUMNS.filter((column) => column.key !== "actions");

const ACCOUNT_TOOLBAR = (
  <SearchInput label="Search accounts" placeholder="Search accounts" containerClassName="w-full sm:w-72" />
);

function AccountsTable({
  selectable = false,
  initiallySelected = [],
  ...props
}: Partial<Parameters<typeof DataTable<Account>>[0]> & { selectable?: boolean; initiallySelected?: string[] }) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set(initiallySelected));
  return (
    <Card className="p-4 md:p-5">
      <DataTable<Account>
        caption="All accounts"
        columns={ACCOUNT_COLUMNS}
        rows={ACCOUNTS}
        rowKey={(account) => account.id}
        toolbar={ACCOUNT_TOOLBAR}
        {...(selectable
          ? {
              selection: {
                selected,
                label: (account: Account) => account.company,
                onToggle: (key: string, next: boolean) =>
                  setSelected((previous) => {
                    const copy = new Set(previous);
                    if (next) copy.add(key);
                    else copy.delete(key);
                    return copy;
                  }),
                onToggleAll: (next: boolean) => setSelected(next ? new Set(ACCOUNTS.map((account) => account.id)) : new Set()),
              },
            }
          : {})}
        {...props}
      />
    </Card>
  );
}

/** The header stays and skeleton rows hold the body's height, so nothing moves when rows land. */
export const Loading: Story = { render: () => <AccountsTable rows={[]} loading loadingRows={5} /> };

/** The fetch failed: the error takes the body's place, and the toolbar that might fix it stays. */
export const Failed: Story = {
  render: () => (
    <AccountsTable
      errorState={
        <ErrorState
          title="Accounts didn't load"
          message="The console service didn't answer. Nothing was changed."
          action={
            <Button size="sm" variant="outline">
              Try again
            </Button>
          }
        />
      }
    />
  ),
};

/** Ticked rows bring up the bulk-action bar: the count, Clear selection, and the caller's actions. */
export const WithBulkActions: Story = {
  render: () => (
    <AccountsTable
      selectable
      initiallySelected={["a-2", "a-4"]}
      bulkActions={
        <>
          <Button size="sm" variant="outline">
            Change plan
          </Button>
          <Button size="sm" variant="outline">
            Export
          </Button>
        </>
      }
    />
  ),
};

/** A long page of rows in its own scroll area, the header pinned; the area is a Tab stop. */
export const StickyHeader: Story = {
  render: () => (
    <AccountsTable
      stickyHeader
      rows={Array.from({ length: 36 }, (_, index) => {
        const base = ACCOUNTS[index % ACCOUNTS.length] ?? ACCOUNTS[0];
        if (base === undefined) throw new Error("no accounts");
        return { ...base, id: `s-${index}`, company: `${base.company} ${index + 1}` };
      })}
    />
  ),
};

/**
 * Below 768px each row is a generated card: title from the first column, badges from the
 * `status` columns, figures from the rest, `RowActions` at the foot; "Created" is `hidden`.
 */
export const MobileCards: Story = {
  render: () => <AccountsTable selectable initiallySelected={["a-1"]} mobileCard="auto" />,
};

/**
 * A custom card, handed the wiring so it opens and marks exactly as its row would. The rows open
 * a record, so no cell holds a control of its own (a button inside a button-row is not operable).
 */
export const MobileCardsCustom: Story = {
  render: () => (
    <AccountsTable
      columns={ACCOUNT_COLUMNS_PLAIN}
      mobileCard={(account, wiring) => (
        <MobileDataCard
          {...wiring}
          title={account.company}
          subtitle={account.domain}
          status={<Tag tone="info">{account.plan}</Tag>}
          fields={[
            { key: "users", label: "Users", value: account.users },
            { key: "revenue", label: "Revenue", value: inr.format(account.revenue) },
          ]}
        />
      )}
      onRowClick={fn()}
      isSelected={(account) => account.id === "a-3"}
    />
  ),
};

export const MobileCardsLoading: Story = {
  render: () => <AccountsTable rows={[]} loading loadingRows={3} mobileCard="auto" />,
};

export const LongText: Story = {
  args: {
    rows: [
      {
        id: "d-9",
        name: "Multi-year enterprise agreement covering warehouse automation, fleet telematics, cold-chain sensors and a dedicated support desk across every region",
        company: "Consolidated Northern & Southern Agricultural Cooperative Federation Private Limited",
        owner: "Oluwaseun Adeyemi-Carter",
        stage: "Negotiation",
        value: 123456789,
        closes: "31 Dec 2026",
      },
      ...DEALS.slice(0, 2),
    ],
  },
};
