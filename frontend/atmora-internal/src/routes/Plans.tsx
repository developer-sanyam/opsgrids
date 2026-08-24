import { useId, useMemo, useState } from "react";

import { getCatalog, listOrganizations, postOverride } from "@/api/client";
import type {
  OrganizationRow,
  OverrideResult,
  Plan,
  PlanVersion,
  Price,
  Product,
} from "@/api/types";
import { formatDate, formatInt } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import {
  Button,
  Card,
  DataTable,
  EnvBadge,
  ErrorState,
  Money,
  Modal,
  PageHeader,
  Pill,
  PlanVersionPill,
  SectionHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { CheckCircleIcon, PlusIcon, TagIcon } from "@/ui/icons";

function priceLabel(price: Price): string {
  if (price.kind === "flat") return `${formatMoney(price.amount_minor, price.currency)} / mo`;
  if (price.kind === "per_seat") return `${formatMoney(price.amount_minor, price.currency)} / ${price.unit ?? "seat"}`;
  const rate = price.unit_price_micros ?? 0;
  return `${formatMoney(Math.round(rate / 10_000), price.currency, { precise: true })} / ${price.unit ?? "unit"}`;
}

function ProductSection({ product, versions }: { product: Product; versions: PlanVersion[] }) {
  const columns: Column<PlanVersion>[] = [
    {
      key: "plan",
      header: "Plan · version",
      render: (v) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{v.plan_name}</span>
          <span className="mono text-xs text-faint">v{v.version_no}</span>
        </div>
      ),
    },
    { key: "state", header: "State", render: (v) => <PlanVersionPill state={v.state} /> },
    {
      key: "audience",
      header: "Audience",
      render: (v) =>
        v.restricted_to_org_id ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent-ink">
            <TagIcon className="size-3" /> {v.restricted_to_org_name ?? "Bespoke"}
          </span>
        ) : (
          <span className="text-sm text-muted">Public</span>
        ),
    },
    {
      key: "price",
      header: "Price",
      render: (v) => {
        const flat = v.prices.find((p) => p.kind === "flat");
        const extras = v.prices.filter((p) => p.kind !== "flat");
        return (
          <div className="flex flex-col gap-1">
            <span className="mono font-semibold text-ink">
              {flat ? priceLabel(flat) : "—"}
            </span>
            {extras.length > 0 ? (
              <span className="flex flex-wrap gap-1">
                {extras.map((p) => (
                  <Tag key={p.price_id}>{priceLabel(p)}</Tag>
                ))}
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "published",
      header: "Published",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (v) => <span className="tnum text-muted">{v.published_at ? formatDate(v.published_at) : "—"}</span>,
    },
    {
      key: "subs",
      header: "Subscribers",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (v) => <span className="tnum text-ink-soft">{formatInt(v.subscriber_count)}</span>,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title={product.name} subtitle={product.description} />
      <DataTable
        columns={columns}
        rows={versions}
        rowKey={(v) => v.plan_version_id}
        caption={`${product.name} plan versions`}
        dense
      />
    </Card>
  );
}

interface CreateBespokeModalProps {
  products: Product[];
  plans: Plan[];
  organizations: OrganizationRow[];
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

function CreateBespokeModal({ products, plans, organizations, onClose, onDone }: CreateBespokeModalProps) {
  const orgId = useId();
  const productId = useId();
  const planId = useId();
  const amountId = useId();
  const reasonId = useId();

  const [organization, setOrganization] = useState(() => organizations[0]?.organization_id ?? "");
  const [productKey, setProductKey] = useState(() => products[0]?.product_key ?? "");
  const productPlans = useMemo(() => plans.filter((p) => p.product_key === productKey), [plans, productKey]);
  const [basePlanId, setBasePlanId] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OverrideResult | null>(null);

  const resolvedPlanId = basePlanId || productPlans[0]?.plan_id || "";
  const amountMinor = Math.round(Number(amount) * 100);
  const selectedOrg = organizations.find((o) => o.organization_id === organization);
  const currency = selectedOrg?.currency ?? "INR";
  const valid = organization !== "" && productKey !== "" && resolvedPlanId !== "" && amountMinor > 0 && reason.trim().length > 0;

  const INPUT =
    "h-11 rounded-tile bg-ground px-3 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await postOverride({
        kind: "bespoke_price",
        organization_id: organization,
        product_key: productKey,
        base_plan_id: resolvedPlanId,
        amount_minor: amountMinor,
        currency,
        reason: reason.trim(),
      });
      setResult(res);
      onDone(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The override was rejected.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Create a bespoke price"
      description="An audience-of-one plan version, pinned to a single customer. No shared plan is re-priced."
      icon={<TagIcon className="size-5" />}
    >
      {result ? (
        <div className="flex flex-col items-center gap-4 py-4 text-center">
          <span className="grid size-14 place-items-center rounded-full bg-active-soft text-active">
            <CheckCircleIcon className="size-7" />
          </span>
          <div className="flex flex-col gap-1">
            <p className="text-base font-bold">Bespoke version created</p>
            <p className="text-sm text-muted">{result.audit.summary}</p>
            <p className="mono text-xs text-faint">{result.override_id}</p>
          </div>
          <Button onClick={onClose} className="w-full">
            Done
          </Button>
        </div>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          {error ? (
            <p role="alert" className="rounded-tile bg-suspended-soft px-3 py-2.5 text-sm font-medium text-suspended">
              {error}
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <label htmlFor={orgId} className="text-sm font-semibold text-ink-soft">
              Customer
            </label>
            <select id={orgId} value={organization} onChange={(e) => setOrganization(e.target.value)} className={`${INPUT} cursor-pointer`}>
              {organizations.map((o) => (
                <option key={o.organization_id} value={o.organization_id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <label htmlFor={productId} className="text-sm font-semibold text-ink-soft">
                Product
              </label>
              <select
                id={productId}
                value={productKey}
                onChange={(e) => {
                  setProductKey(e.target.value);
                  setBasePlanId("");
                }}
                className={`${INPUT} cursor-pointer`}
              >
                {products.map((p) => (
                  <option key={p.product_key} value={p.product_key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor={planId} className="text-sm font-semibold text-ink-soft">
                Base plan to clone
              </label>
              <select id={planId} value={resolvedPlanId} onChange={(e) => setBasePlanId(e.target.value)} className={`${INPUT} cursor-pointer`}>
                {productPlans.map((p) => (
                  <option key={p.plan_id} value={p.plan_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={amountId} className="text-sm font-semibold text-ink-soft">
              Bespoke monthly price (₹)
            </label>
            <input
              id={amountId}
              type="number"
              min="1"
              step="1"
              required
              inputMode="numeric"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="420000"
              className={`${INPUT} tnum`}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={reasonId} className="text-sm font-semibold text-ink-soft">
              Reason (required)
            </label>
            <input
              id={reasonId}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Negotiated enterprise rate"
              className={INPUT}
            />
          </div>

          {amountMinor > 0 && selectedOrg ? (
            <p className="text-sm text-muted">
              Pins {selectedOrg.name} to a new version at{" "}
              <Money minor={amountMinor} currency={currency} className="font-semibold text-ink" /> / mo.
            </p>
          ) : null}

          <p className="rounded-tile bg-ground px-3 py-2.5 text-xs leading-relaxed text-muted">
            POSTs <span className="mono">catalog.bespoke_price.created</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span> → catalog-service clones the plan
            with <span className="mono">restricted_to_org_id</span> set and pins the subscription.
          </p>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !valid}>
              {submitting ? "Creating…" : "Create bespoke price"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

const NO_PRODUCT: string = "all";
const EMPTY_PRODUCTS: Product[] = [];

export function PlansPage() {
  const catalog = useAsync(getCatalog, []);
  const orgs = useAsync(() => listOrganizations(), []);
  const [productFilter, setProductFilter] = useState<string>(NO_PRODUCT);
  const [showDrafts, setShowDrafts] = useState(true);
  const [creating, setCreating] = useState(false);
  const [banner, setBanner] = useState<OverrideResult | null>(null);

  const products = useMemo(
    () => (catalog.status === "success" ? catalog.data.products : EMPTY_PRODUCTS),
    [catalog],
  );
  const orgList = orgs.status === "success" ? orgs.data.items : [];

  const shownProducts = useMemo(
    () => (productFilter === NO_PRODUCT ? products : products.filter((p) => p.product_key === productFilter)),
    [products, productFilter],
  );

  function versionsFor(productKey: string): PlanVersion[] {
    if (catalog.status !== "success") return [];
    return catalog.data.versions
      .filter((v) => v.product_key === productKey && (showDrafts || v.state !== "draft"))
      .sort((a, b) => a.plan_name.localeCompare(b.plan_name) || b.version_no - a.version_no);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Catalog"
        title="Plans & Pricing"
        description="The global, immutable catalog. Every published version is frozen — a new price is a new version."
        actions={
          <div className="flex items-center gap-2">
            <EnvBadge />
            <Button
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => setCreating(true)}
              disabled={catalog.status !== "success" || orgs.status !== "success"}
            >
              Bespoke price
            </Button>
          </div>
        }
      />

      {banner ? (
        <div role="status" className="flex items-center gap-3 rounded-card bg-active-soft px-4 py-3 text-sm text-active">
          <CheckCircleIcon className="size-5 shrink-0" />
          <span className="font-semibold">{banner.audit.summary}</span>
          <button type="button" onClick={() => setBanner(null)} className="ml-auto text-xs font-semibold text-active/70 hover:text-active">
            Dismiss
          </button>
        </div>
      ) : null}

      {catalog.status === "loading" ? (
        <div className="flex flex-col gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-56 w-full" />
          ))}
        </div>
      ) : catalog.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={catalog.error.message} />
        </Card>
      ) : (
        <>
          <Card className="flex flex-wrap items-center gap-3 p-4 sm:p-5">
            <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1">
              <Pill active={productFilter === NO_PRODUCT} onClick={() => setProductFilter(NO_PRODUCT)}>
                All products
              </Pill>
              {products.map((p) => (
                <Pill key={p.product_key} active={productFilter === p.product_key} onClick={() => setProductFilter(p.product_key)}>
                  {p.name}
                </Pill>
              ))}
            </div>
            <label className="ml-auto inline-flex items-center gap-2 text-sm font-semibold text-ink-soft">
              <input
                type="checkbox"
                checked={showDrafts}
                onChange={(e) => setShowDrafts(e.target.checked)}
                className="size-4 accent-[var(--color-accent)]"
              />
              Show drafts
            </label>
          </Card>

          <div className="flex flex-col gap-5">
            {shownProducts.map((product) => (
              <ProductSection key={product.product_key} product={product} versions={versionsFor(product.product_key)} />
            ))}
          </div>
        </>
      )}

      {creating ? (
        <CreateBespokeModal
          products={products}
          plans={catalog.status === "success" ? catalog.data.plans : []}
          organizations={orgList}
          onClose={() => setCreating(false)}
          onDone={(res) => setBanner(res)}
        />
      ) : null}
    </div>
  );
}
