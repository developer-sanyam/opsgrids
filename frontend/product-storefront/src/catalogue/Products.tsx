import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  RowActions,
  SampleChip,
  SearchInput,
  Select,
  Skeleton,
  Tag,
  useToast,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { USE_FIXTURES } from "@/api/client";
import { formatPriceBand } from "@/lib/format";
import { ProductForm } from "./ProductForm";
import { deleteProduct, listCategories, listProducts, updateProduct } from "./api";
import type { ProductOut, ProductSort, ProductStatus } from "./types";

const WRITE = "storefront.catalogue.product.write";
const READ = "storefront.catalogue.product.read";

const STATUS_FILTER_OPTIONS = [
  { value: "", label: "Any status" },
  { value: "draft", label: "Draft" },
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

export function ProductsPage() {
  const toast = useToast();
  const canWrite = useCan(WRITE, "ORG");
  const canRead = useCan(READ, "ORG");
  const [params] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState<ProductStatus | "">("");
  const [categoryId, setCategoryId] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "updated_at", dir: "desc" });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState<ProductOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ProductOut | null>(null);

  const categories = useAsync(() => listCategories({ size: 200 }), []);
  const categoryOptions =
    categories.status === "success"
      ? [{ value: "", label: "Any category" }, ...categories.data.items.map((c) => ({ value: c.id, label: c.label }))]
      : [{ value: "", label: "Any category" }];

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

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Products"
        description="Your catalogue — what buyers see on the storefront, and what the theme's product sections draw from."
        actions={USE_FIXTURES ? <SampleChip title="Representative data — running on local fixtures" /> : null}
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
                toolbar={
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <SearchInput
                        label="Search products"
                        placeholder="Code, name or brand"
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value);
                          setPage(1);
                        }}
                      />
                      <Select
                        label="Status"
                        value={status}
                        options={STATUS_FILTER_OPTIONS}
                        onChange={(e) => {
                          setStatus(e.target.value as ProductStatus | "");
                          setPage(1);
                        }}
                        containerClassName="w-40"
                      />
                      <Select
                        label="Category"
                        value={categoryId}
                        options={categoryOptions}
                        onChange={(e) => {
                          setCategoryId(e.target.value);
                          setPage(1);
                        }}
                        containerClassName="w-48"
                      />
                    </div>
                    <Can permission={WRITE} scope="ORG">
                      <Button onClick={() => setCreating(true)} disabled={categories.status !== "success" || categories.data.items.length === 0}>
                        New product
                      </Button>
                    </Can>
                  </div>
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
                        <Button
                          variant="outline"
                          onClick={() => {
                            setQuery("");
                            setStatus("");
                            setCategoryId("");
                          }}
                        >
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
                      render: (p) => (
                        <Tag tone={p.status === "active" ? "positive" : p.status === "archived" ? "neutral" : "warning"}>
                          {p.status}
                        </Tag>
                      ),
                    },
                    {
                      key: "in_stock",
                      header: "Stock",
                      render: (p) => <Tag tone={p.in_stock ? "positive" : "warning"}>{p.in_stock ? "In stock" : "Out of stock"}</Tag>,
                    },
                    {
                      key: "actions",
                      header: "",
                      align: "right",
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
