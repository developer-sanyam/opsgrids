import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  FilterBar,
  PageHeader,
  Pagination,
  RowActions,
  SampleChip,
  Skeleton,
  StatusBadge,
  statusTone,
  useToast,
} from "@opsgrids/ui";
import type { Column, StatusTone, TableSelection } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/api/client";
import { downloadCsv } from "@/lib/csv";
import { formatPriceBand } from "@/lib/format";
import { ProductForm } from "./ProductForm";
import { deleteProduct, listCategories, listProducts, updateProduct } from "./api";
import type { ProductOut, ProductSort, ProductStatus } from "./types";

/**
 * `draft` here is a plain in-progress state, not the at-risk sense the shared word registry gives
 * some products' drafts — kept as this app has always shown it. See
 * `opsgrids-ui/src/lib/status.ts`.
 */
const STATUS_TONE_OVERRIDE: Partial<Record<ProductStatus, StatusTone>> = {
  draft: "warning",
};

const WRITE = "storefront.catalogue.product.write";
const READ = "storefront.catalogue.product.read";

/** What a status is called on screen. The wire value is a lowercase slug and never shown as one. */
const STATUS_LABEL: Record<ProductStatus, string> = {
  draft: "Draft",
  active: "Active",
  archived: "Archived",
};

const STATUS_FILTER_OPTIONS = [
  { value: "draft", label: STATUS_LABEL.draft },
  { value: "active", label: STATUS_LABEL.active },
  { value: "archived", label: STATUS_LABEL.archived },
];

export function ProductsPage() {
  const toast = useToast();
  const canWrite = useCan(WRITE, "ORG");
  const canRead = useCan(READ, "ORG");
  const [params] = useSearchParams();
  // `FilterBar` debounces the typing itself and hands back the committed query.
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState<ProductStatus | "">("");
  const [categoryId, setCategoryId] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "updated_at", dir: "desc" });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState<ProductOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ProductOut | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const categories = useAsync(() => listCategories({ size: 200 }), []);
  const categoryOptions =
    categories.status === "success"
      ? categories.data.items.map((c) => ({ value: c.id, label: c.label }))
      : [];

  const sortKey = (sort.dir === "desc" ? `-${sort.key}` : sort.key) as ProductSort;
  const products = useAsync(
    () =>
      listProducts({
        q: query,
        ...(status ? { status } : {}),
        ...(categoryId ? { category_id: categoryId } : {}),
        sort: sortKey,
        page,
        size,
      }),
    [query, status, categoryId, sortKey, page, size],
  );

  const remove = useMutation((product: ProductOut) => deleteProduct(product.id), {
    onSuccess: (_result, product) => {
      setDeleting(null);
      toast.success(`${product.name} deleted.`);
      products.reload();
    },
  });

  async function archiveToggle(product: ProductOut) {
    try {
      await updateProduct(product.id, { status: product.status === "archived" ? "active" : "archived" });
      toast.success(product.status === "archived" ? `${product.name} unarchived.` : `${product.name} archived.`);
      products.reload();
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "That didn't work. Try again.");
    }
  }

  function clearFilters() {
    setQuery("");
    setStatus("");
    setCategoryId("");
    setPage(1);
  }

  const productSelection: TableSelection<ProductOut> | undefined =
    products.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) =>
            setSelected(next ? new Set(products.data.items.map((p) => p.id)) : new Set()),
          label: (p) => p.name,
        }
      : undefined;

  function exportSelectedProducts() {
    if (products.status !== "success") return;
    const chosen = products.data.items.filter((p) => selected.has(p.id));
    downloadCsv(
      "products.csv",
      ["Product", "Code", "Category", "Price", "Status", "Stock"],
      chosen.map((p) => [
        p.name,
        p.code,
        p.category,
        formatPriceBand(p),
        p.status,
        p.in_stock ? "In stock" : "Out of stock",
      ]),
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Products"
        description="Your catalogue — what buyers see on the storefront, and what the theme's product sections draw from."
        actions={
          <>
            {USE_FIXTURES ? <SampleChip title="Representative data — running on local fixtures" /> : null}
            <Can permission={WRITE} scope="ORG">
              <Button
                onClick={() => setCreating(true)}
                disabled={categories.status !== "success" || categories.data.items.length === 0}
              >
                New product
              </Button>
            </Can>
          </>
        }
      />

      {!canRead ? (
        <ErrorState
          tone="neutral"
          title="You can't see the catalogue"
          message="This needs storefront.catalogue.product.read at organization scope. An owner or a storefront admin can grant it."
        />
      ) : (
        <>
          {products.status === "loading" ? <Skeleton className="h-64 w-full" /> : null}
          {products.status === "error" ? (
            <ErrorState
              title="Couldn't load products"
              message={problemMessage(products.error)}
              action={<Button onClick={products.reload}>Try again</Button>}
            />
          ) : null}

          {products.status === "success" ? (
            <>
              <DataTable
                rows={products.data.items}
                rowKey={(p) => p.id}
                sort={sort}
                onSort={(key) => setSort((s) => ({ key, dir: s.key === key && s.dir === "asc" ? "desc" : "asc" }))}
                mobileCard="auto"
                selection={productSelection}
                bulkActions={
                  <Button variant="outline" size="sm" onClick={exportSelectedProducts}>
                    Export selected
                  </Button>
                }
                toolbar={
                  <FilterBar
                    label="Filter products"
                    search={{
                      label: "Search products",
                      placeholder: "Code, name or brand",
                      value: query,
                      onChange: (value) => {
                        setQuery(value);
                        setPage(1);
                      },
                    }}
                    filters={[
                      {
                        key: "status",
                        label: "Status",
                        allLabel: "Any status",
                        options: STATUS_FILTER_OPTIONS,
                        value: status,
                        onChange: (value) => {
                          setStatus(value as ProductStatus | "");
                          setPage(1);
                        },
                      },
                      {
                        key: "category",
                        label: "Category",
                        allLabel: "Any category",
                        options: categoryOptions,
                        value: categoryId,
                        onChange: (value) => {
                          setCategoryId(value);
                          setPage(1);
                        },
                      },
                    ]}
                    onClearAll={clearFilters}
                  />
                }
                emptyState={
                  <EmptyState
                    title={query || status || categoryId ? "No products match" : "No products yet"}
                    description={
                      query || status || categoryId
                        ? "Try clearing a filter."
                        : categories.status === "success" && categories.data.items.length === 0
                          ? "Create a category first, then add your first product."
                          : "Create the first one to get started."
                    }
                    action={
                      query || status || categoryId ? (
                        <Button variant="outline" onClick={clearFilters}>
                          Clear filters
                        </Button>
                      ) : canWrite && categories.status === "success" && categories.data.items.length > 0 ? (
                        <Button onClick={() => setCreating(true)}>New product</Button>
                      ) : null
                    }
                  />
                }
                columns={
                  [
                    {
                      key: "name",
                      header: "Product",
                      sortKey: "name",
                      mobile: "title",
                      render: (p) => (
                        <div className="flex flex-col gap-0.5">
                          <span className="font-bold text-ink">{p.name}</span>
                          <span className="text-xs text-faint tnum">{p.code}</span>
                        </div>
                      ),
                    },
                    { key: "category", header: "Category", render: (p) => p.category },
                    {
                      key: "price_min_minor",
                      header: "Price",
                      sortKey: "price_min_minor",
                      cellClassName: "tnum",
                      render: (p) => formatPriceBand(p),
                    },
                    {
                      key: "status",
                      header: "Status",
                      mobile: "status",
                      render: (p) => (
                        <StatusBadge tone={STATUS_TONE_OVERRIDE[p.status] ?? statusTone(p.status) ?? "neutral"} label={STATUS_LABEL[p.status]} />
                      ),
                    },
                    {
                      key: "in_stock",
                      header: "Stock",
                      render: (p) => (
                        <StatusBadge tone={p.in_stock ? "success" : "warning"} label={p.in_stock ? "In stock" : "Out of stock"} />
                      ),
                    },
                    {
                      key: "actions",
                      header: "",
                      align: "right",
                      mobile: "actions",
                      render: (p) => (
                        <RowActions
                          label={p.name}
                          actions={[
                            { label: "Edit", onSelect: () => setEditing(p), hidden: !canWrite },
                            {
                              label: p.status === "archived" ? "Unarchive" : "Archive",
                              onSelect: () =>
                                void archiveToggle(p),
                              hidden: !canWrite,
                            },
                            { label: "Delete", tone: "danger", onSelect: () => setDeleting(p), hidden: !canWrite },
                          ]}
                        />
                      ),
                    },
                  ] satisfies Column<ProductOut>[]
                }
              />
              <Pagination
                page={page}
                pages={products.data.pages}
                total={products.data.total}
                size={size}
                noun="products"
                onPageChange={setPage}
                onSizeChange={(n) => {
                  setSize(n);
                  setPage(1);
                }}
              />
            </>
          ) : null}
        </>
      )}

      {creating && categories.status === "success" ? (
        <ProductForm
          product={null}
          categories={categories.data.items}
          onClose={() => setCreating(false)}
          onSaved={(p) => {
            toast.success(`${p.name} created.`);
            products.reload();
          }}
        />
      ) : null}
      {editing && categories.status === "success" ? (
        <ProductForm
          product={editing}
          categories={categories.data.items}
          onClose={() => setEditing(null)}
          onSaved={(p) => {
            toast.success(`${p.name} saved.`);
            products.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this product?"
        body={`${deleting?.name} is removed. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error instanceof ApiError ? { error: remove.error.message } : {})}
      />
    </div>
  );
}
