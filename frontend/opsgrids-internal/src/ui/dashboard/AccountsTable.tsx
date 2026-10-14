import { useMemo, useState } from "react";

import {
  Avatar,
  BuildingIcon,
  Card,
  CloseIcon,
  DataTable,
  EmptyState,
  ErrorState,
  EyeIcon,
  FilterBar,
  Menu,
  MobileDataCard,
  Pagination,
  PauseCircleIcon,
  RefreshIcon,
  SearchIcon,
  SearchInput,
  StatusBadge,
  Tag,
  TagIcon,
} from "@opsgrids/ui";
import type { Column, FilterSelect, MenuItem, MobileCardWiring } from "@opsgrids/ui";

import type { OrgStateOut, SubscriptionOut } from "@/api/types";
import type { AsyncState } from "@/lib/useAsync";
import { formatDate, formatInt } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { lifecycleLabel, productLabel, regionLabel } from "@/lib/labels";

type SortKey = "users" | "revenue" | "created";
type Sort = { key: SortKey; dir: "asc" | "desc" };

const PAGE_SIZES = [10, 25, 50];

interface AccountsTableProps {
  orgs: AsyncState<OrgStateOut[]>;
  onRetry: () => void;
  /** `null` (once loaded) means the operator's role never let the console ask — see `canSeeRevenue`. */
  subscriptions: AsyncState<SubscriptionOut[] | null>;
  canSeeRevenue: boolean;
  canWriteOverrides: boolean;
  onView: (org: OrgStateOut) => void;
  onPricing: (org: OrgStateOut) => void;
  onSuspend: (org: OrgStateOut) => void;
  onReactivate: (org: OrgStateOut) => void;
}

function orgLabel(org: OrgStateOut): string {
  return org.name ?? org.slug ?? org.organization_id;
}

function CompanyCell({ org }: { org: OrgStateOut }) {
  const name = orgLabel(org);
  return (
    <div className="flex min-w-0 items-center gap-3">
      <Avatar name={name} size="sm" />
      <div className="flex min-w-0 flex-col leading-tight">
        <span className="truncate text-sm font-semibold text-ink">{name}</span>
        {org.slug ? <span className="truncate text-xs text-muted">{org.slug}</span> : null}
      </div>
    </div>
  );
}

function DomainCell({ slug }: { slug: string | null }) {
  if (!slug) return <span className="text-sm text-faint">—</span>;
  return (
    <span className="block max-w-32 truncate text-sm text-muted" title={slug}>
      {slug}
    </span>
  );
}

function StatusCell({ lifecycle }: { lifecycle: OrgStateOut["lifecycle"] }) {
  if (lifecycle === "none") return <StatusBadge tone="neutral" label="No subscription" />;
  return <StatusBadge status={lifecycle} />;
}

/** The Plan column: with billing visibility, the product and plan names off `/admin/subscriptions`
 *  ("CRM · Growth", or "{n} products" once there are more than one); without it, the coarser
 *  `active_products` every operator role already gets from `/admin/organizations`. */
function PlanCell({
  org,
  subsByOrg,
  canSeeRevenue,
}: {
  org: OrgStateOut;
  subsByOrg: Map<string, SubscriptionOut[]>;
  canSeeRevenue: boolean;
}) {
  if (canSeeRevenue) {
    const subs = subsByOrg.get(org.organization_id) ?? [];
    if (subs.length === 0) return <span className="text-sm text-faint">—</span>;
    const label = (s: SubscriptionOut) => {
      const product = s.product_name ?? productLabel(s.product_key);
      return s.plan_name ? `${product} · ${s.plan_name}` : product;
    };
    if (subs.length === 1) {
      const only = subs[0];
      return <span className="text-sm font-medium text-ink">{only ? label(only) : "—"}</span>;
    }
    return (
      <span className="text-sm font-medium text-ink" title={subs.map(label).join(", ")}>
        {subs.length} products
      </span>
    );
  }

  if (org.active_products.length === 0) return <span className="text-sm text-faint">—</span>;
  if (org.active_products.length <= 2) {
    return (
      <span className="flex flex-wrap gap-1">
        {org.active_products.map((p) => (
          <Tag key={p}>{productLabel(p)}</Tag>
        ))}
      </span>
    );
  }
  return (
    <span className="text-sm font-medium text-ink" title={org.active_products.map(productLabel).join(", ")}>
      {org.active_products.length} products
    </span>
  );
}

/** The phone row's plan pill (mock-1): the plan name alone where one is known, never the
 *  "Product · Plan" pairing `PlanCell` reads for a full table cell — a chip beside a status chip
 *  has no room for both. */
function planChipLabel(
  org: OrgStateOut,
  subsByOrg: Map<string, SubscriptionOut[]>,
  canSeeRevenue: boolean,
): string | null {
  if (canSeeRevenue) {
    const subs = subsByOrg.get(org.organization_id) ?? [];
    if (subs.length === 0) return null;
    if (subs.length > 1) return `${subs.length} products`;
    const only = subs[0];
    return only ? (only.plan_name ?? productLabel(only.product_key)) : null;
  }
  if (org.active_products.length === 0) return null;
  if (org.active_products.length > 1) return `${org.active_products.length} products`;
  const only = org.active_products[0];
  return only ? productLabel(only) : null;
}

function RevenueCell({ org, canSeeRevenue }: { org: OrgStateOut; canSeeRevenue: boolean }) {
  if (!canSeeRevenue) {
    return (
      <span className="text-sm text-faint" title="Needs the owner or billing role">
        Restricted
      </span>
    );
  }
  if (org.mrr_minor <= 0) return <span className="text-sm text-faint">—</span>;
  return <span className="tnum font-semibold text-ink">{formatMoney(org.mrr_minor, org.currency ?? "INR")}</span>;
}

/**
 * The phone header's search (mock-1): an icon button that expands into a real search field in
 * place of the heading, rather than a search box sitting open above the list at all times — there
 * is no room for one beside "Accounts (128)" at this width. Collapsing clears the query, the same
 * as `FilterBar`'s own "Clear all" does for the desktop search it stands in for here.
 */
function MobileAccountSearch({
  open,
  onOpenChange,
  value,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onChange: (value: string) => void;
}) {
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onOpenChange(true)}
        aria-label="Search accounts"
        className="grid size-11 shrink-0 place-items-center rounded-tile border border-field-edge bg-surface text-ink-soft transition-colors hover:bg-ground"
      >
        <SearchIcon className="size-4.5" />
      </button>
    );
  }

  return (
    <div className="flex w-full items-center gap-1">
      <SearchInput
        label="Search accounts"
        placeholder="Search accounts, domain, owner…"
        value={value}
        onDebouncedChange={onChange}
        containerClassName="h-10 flex-1"
        // Mounted only by the user's own tap on the search icon; moving focus into the field they
        // asked for is the focus management, not a surprise on page load.
        // eslint-disable-next-line jsx-a11y/no-autofocus
        autoFocus
      />
      <button
        type="button"
        onClick={() => {
          onOpenChange(false);
          onChange("");
        }}
        aria-label="Close search"
        className="grid size-11 shrink-0 place-items-center rounded-tile text-ink-soft transition-colors hover:bg-ground"
      >
        <CloseIcon className="size-4.5" />
      </button>
    </div>
  );
}

function compareOrgs(a: OrgStateOut, b: OrgStateOut, sort: Sort): number {
  let cmp = 0;
  if (sort.key === "users") cmp = (a.user_count ?? -1) - (b.user_count ?? -1);
  else if (sort.key === "revenue") cmp = a.mrr_minor - b.mrr_minor;
  else cmp = (a.created_at ?? "").localeCompare(b.created_at ?? "");
  return sort.dir === "asc" ? cmp : -cmp;
}

/** The mockup's "All Accounts" table: search, Plan/Status/Region filters, the account list, and
 *  client-side pagination — the scale this console operates at makes a client-side page perfectly
 *  fine, the same call `routes/Accounts.tsx` already makes for the same list. */
export function AccountsTable({
  orgs,
  onRetry,
  subscriptions,
  canSeeRevenue,
  canWriteOverrides,
  onView,
  onPricing,
  onSuspend,
  onReactivate,
}: AccountsTableProps) {
  const [search, setSearch] = useState("");
  const [plan, setPlan] = useState("");
  const [status, setStatus] = useState("");
  const [region, setRegion] = useState("");
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [sort, setSort] = useState<Sort>({ key: "created", dir: "desc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const rows = useMemo(() => (orgs.status === "success" ? orgs.data : []), [orgs]);

  const subsByOrg = useMemo(() => {
    const map = new Map<string, SubscriptionOut[]>();
    if (subscriptions.status === "success" && subscriptions.data) {
      for (const sub of subscriptions.data) {
        const list = map.get(sub.organization_id);
        if (list) list.push(sub);
        else map.set(sub.organization_id, [sub]);
      }
    }
    return map;
  }, [subscriptions]);

  const planOptions = useMemo(
    () =>
      [...new Set(rows.flatMap((o) => o.active_products))]
        .sort()
        .map((key) => ({ value: key, label: productLabel(key) })),
    [rows],
  );
  const statusOptions = useMemo(
    () =>
      [...new Set(rows.map((o) => o.lifecycle))].sort().map((lifecycle) => ({
        value: lifecycle,
        label: lifecycleLabel(lifecycle),
      })),
    [rows],
  );
  const regionOptions = useMemo(
    () =>
      [...new Set(rows.map((o) => o.country).filter((c): c is string => c !== null))]
        .sort()
        .map((code) => ({ value: code, label: regionLabel(code) })),
    [rows],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((org) => {
      const matchesQuery =
        q === "" ||
        orgLabel(org).toLowerCase().includes(q) ||
        (org.slug?.toLowerCase().includes(q) ?? false) ||
        org.organization_id.toLowerCase().includes(q);
      const matchesPlan = plan === "" || org.active_products.includes(plan);
      const matchesStatus = status === "" || org.lifecycle === status;
      const matchesRegion = region === "" || org.country === region;
      return matchesQuery && matchesPlan && matchesStatus && matchesRegion;
    });
  }, [rows, search, plan, status, region]);

  const sorted = useMemo(
    () => [...filtered].sort((a, b) => compareOrgs(a, b, sort)),
    [filtered, sort],
  );

  const pages = sorted.length === 0 ? 0 : Math.ceil(sorted.length / pageSize);
  const safePage = Math.min(Math.max(page, 1), Math.max(pages, 1));
  const pageRows = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  function resetPage() {
    setPage(1);
  }

  const filters: FilterSelect[] = [
    { key: "plan", label: "Plan", options: planOptions, value: plan, onChange: (v) => { setPlan(v); resetPage(); }, allLabel: "All Plans" },
    { key: "status", label: "Status", options: statusOptions, value: status, onChange: (v) => { setStatus(v); resetPage(); }, allLabel: "All Status" },
    { key: "region", label: "Region", options: regionOptions, value: region, onChange: (v) => { setRegion(v); resetPage(); }, allLabel: "All Regions" },
  ];

  function actionsFor(org: OrgStateOut): MenuItem[] {
    return [
      { label: "View", icon: <EyeIcon className="size-4" />, onSelect: () => onView(org) },
      { label: "Pricing", icon: <TagIcon className="size-4" />, onSelect: () => onPricing(org) },
      {
        label: "Suspend",
        icon: <PauseCircleIcon className="size-4" />,
        tone: "danger",
        hidden: !canWriteOverrides,
        onSelect: () => onSuspend(org),
      },
      {
        label: "Reactivate",
        icon: <RefreshIcon className="size-4" />,
        hidden: !canWriteOverrides,
        onSelect: () => onReactivate(org),
      },
    ];
  }

  /**
   * A phone's row (mock-1): a logo/initial avatar, the name with its domain underneath, and the
   * plan and status as two chips on the right. Tapping the row opens the account — the same drill
   * down the desktop table's row click does — so there is nowhere here for Users or Revenue, or
   * for a per-row action menu the mockup's list never shows either; both stay one tap away on the
   * account page.
   */
  function accountCard(org: OrgStateOut, wiring: MobileCardWiring) {
    const planText = planChipLabel(org, subsByOrg, canSeeRevenue);
    return (
      <MobileDataCard
        {...wiring}
        // `DataTable` never got an `onRowClick` here (the desktop table opens an account only
        // from its row menu), but the mockup's phone row opens on tap regardless — wired directly
        // rather than by adding one to the whole table, which would also make the desktop row's
        // own "..." menu click bubble into a navigation.
        onOpen={() => onView(org)}
        leading={<Avatar name={orgLabel(org)} size="sm" />}
        title={orgLabel(org)}
        subtitle={org.slug}
        status={
          <>
            {planText ? <Tag tone="info">{planText}</Tag> : null}
            <StatusCell lifecycle={org.lifecycle} />
          </>
        }
      />
    );
  }

  // Below 1280px the table keeps the columns a decision needs — company, plan, status, revenue —
  // and drops the ones a tap on the row already answers (brief §2: "tablet ... fewer columns"),
  // rather than a table left to scroll sideways to be read at all.
  const DESKTOP_ONLY = "hidden xl:table-cell";

  const columns: Column<OrgStateOut>[] = [
    { key: "company", header: "Company", render: (org) => <CompanyCell org={org} /> },
    {
      key: "domain",
      header: "Domain",
      headerClassName: DESKTOP_ONLY,
      cellClassName: DESKTOP_ONLY,
      render: (org) => <DomainCell slug={org.slug} />,
    },
    { key: "plan", header: "Plan", render: (org) => <PlanCell org={org} subsByOrg={subsByOrg} canSeeRevenue={canSeeRevenue} /> },
    { key: "status", header: "Status", render: (org) => <StatusCell lifecycle={org.lifecycle} /> },
    {
      key: "users",
      header: "Users",
      align: "right",
      headerClassName: DESKTOP_ONLY,
      cellClassName: DESKTOP_ONLY,
      sortKey: "users",
      render: (org) => <span className="tnum">{org.user_count !== null ? formatInt(org.user_count) : "—"}</span>,
    },
    {
      key: "revenue",
      header: "Revenue",
      align: "right",
      sortKey: canSeeRevenue ? "revenue" : undefined,
      render: (org) => <RevenueCell org={org} canSeeRevenue={canSeeRevenue} />,
    },
    {
      key: "created",
      header: "Created At",
      align: "right",
      headerClassName: DESKTOP_ONLY,
      cellClassName: DESKTOP_ONLY,
      sortKey: "created",
      render: (org) => <span className="tnum whitespace-nowrap text-muted">{formatDate(org.created_at)}</span>,
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      render: (org) => <Menu label={`Actions for ${orgLabel(org)}`} trigger="icon" items={actionsFor(org)} align="end" />,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className={`text-base font-bold text-ink${mobileSearchOpen ? " hidden md:block" : ""}`}>
          <span className="md:hidden">Accounts</span>
          <span className="hidden md:inline">All Accounts</span>
          {orgs.status === "success" ? ` (${formatInt(filtered.length)})` : ""}
        </h2>
        {/* The phone header (mock-1): a search icon that expands in place of the heading, no "+"
            beside it — there is no create-account API for this console to call. Plan/Status/Region
            stay a `md:`-and-up affordance; the embedded dashboard card is a glance at the list,
            not the full management screen `routes/Accounts.tsx` is. */}
        <div className={mobileSearchOpen ? "flex-1 md:hidden" : "md:hidden"}>
          <MobileAccountSearch
            open={mobileSearchOpen}
            onOpenChange={setMobileSearchOpen}
            value={search}
            onChange={(value) => { setSearch(value); resetPage(); }}
          />
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={pageRows}
        rowKey={(org) => org.organization_id}
        caption="Every account on the platform"
        stickyHeader
        mobileCard={accountCard}
        sort={{ key: sort.key, dir: sort.dir }}
        onSort={(key) =>
          setSort((current) =>
            current.key === key ? { key: key as SortKey, dir: current.dir === "asc" ? "desc" : "asc" } : { key: key as SortKey, dir: "desc" },
          )
        }
        loading={orgs.status === "loading"}
        toolbar={
          <div className="hidden md:block">
            <FilterBar
              label="Filter accounts"
              search={{
                label: "Search accounts",
                placeholder: "Search accounts, domain, owner…",
                value: search,
                onChange: (v) => { setSearch(v); resetPage(); },
              }}
              filters={filters}
              onClearAll={() => {
                setSearch("");
                setPlan("");
                setStatus("");
                setRegion("");
                resetPage();
              }}
            />
          </div>
        }
        errorState={orgs.status === "error" ? <ErrorState message={orgs.error.message} onRetry={onRetry} /> : undefined}
        emptyState={
          <EmptyState
            icon={<BuildingIcon className="size-6" />}
            title="No matching accounts"
            description="No account matches these filters. Try a different status, plan or region, or clear the search."
          />
        }
      />

      {orgs.status === "success" && sorted.length > 0 ? (
        <Pagination
          page={safePage}
          pages={pages}
          total={sorted.length}
          size={pageSize}
          onPageChange={setPage}
          onSizeChange={(size) => {
            setPageSize(size);
            resetPage();
          }}
          sizeOptions={PAGE_SIZES}
          noun="accounts"
        />
      ) : null}
    </Card>
  );
}
