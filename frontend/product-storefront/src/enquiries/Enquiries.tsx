import { useState } from "react";

import { ApiError, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  InlineAlert,
  DataTable,
  PageHeader,
  Pagination,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
  Tag,
  useToast,
} from "@opsgrids/ui";
import type { Column, TagTone } from "@opsgrids/ui";

import { getEnquiry, listEnquiries, updateEnquiryStatus } from "./api";
import { ENQUIRY_TRANSITIONS } from "./types";
import type { EnquiryOut, EnquiryStatus } from "./types";

const WRITE = "storefront.sales.enquiry.write";
const READ = "storefront.sales.enquiry.read";

const STATUS_OPTIONS = [
  { value: "", label: "Any status" },
  { value: "new", label: "New" },
  { value: "contacted", label: "Contacted" },
  { value: "won", label: "Won" },
  { value: "lost", label: "Lost" },
];

const STATUS_TONE: Record<EnquiryStatus, TagTone> = {
  new: "info",
  contacted: "warning",
  won: "positive",
  lost: "neutral",
};

const STATUS_LABEL: Record<EnquiryStatus, string> = {
  new: "New",
  contacted: "Contacted",
  won: "Won",
  lost: "Lost",
};

function EnquiryDetail({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const canWrite = useCan(WRITE, "ORG");
  const detail = useAsync(() => getEnquiry(id), [id]);

  const transition = useMutation(
    (status: EnquiryStatus) => updateEnquiryStatus(id, status),
    {
      onSuccess: (updated) => {
        toast.success(`Marked ${STATUS_LABEL[updated.status].toLowerCase()}.`);
        detail.reload();
        onChanged();
      },
    },
  );

  return (
    <Dialog open onClose={onClose} title="Enquiry" size="lg">
      {detail.status === "loading" ? <Skeleton className="h-64 w-full" /> : null}
      {detail.status === "error" ? (
        <ErrorState
          title="Couldn't load this enquiry"
          message={problemMessage(detail.error)}
          action={<Button onClick={detail.reload}>Try again</Button>}
        />
      ) : null}
      {detail.status === "success" ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-lg font-bold text-ink">{detail.data.name}</p>
              <p className="text-sm text-muted">{detail.data.company}</p>
            </div>
            <Tag tone={STATUS_TONE[detail.data.status]}>{STATUS_LABEL[detail.data.status]}</Tag>
          </div>

          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-faint">Email</dt>
              <dd className="text-sm text-ink">{detail.data.email_masked}</dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-faint">Phone</dt>
              <dd className="text-sm text-ink">{detail.data.phone_masked}</dd>
            </div>
            {detail.data.gstin ? (
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-faint">GSTIN</dt>
                <dd className="text-sm text-ink tnum">{detail.data.gstin}</dd>
              </div>
            ) : null}
            {detail.data.quantity ? (
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-faint">Quantity</dt>
                <dd className="text-sm text-ink">{detail.data.quantity}</dd>
              </div>
            ) : null}
          </dl>

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-faint">Requirement</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-ink">{detail.data.requirement}</p>
          </div>

          {detail.data.lines.length > 0 ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-faint">Line list</p>
              <ul className="mt-1 flex flex-col gap-1">
                {detail.data.lines.map((line, i) => (
                  <li key={`${line.product_id}-${i}`} className="text-sm text-ink">
                    {line.quantity} × {line.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {transition.error ? (
            <InlineAlert tone="danger" title="That didn't work">
              {transition.error instanceof ApiError ? transition.error.message : problemMessage(transition.error)}
            </InlineAlert>
          ) : null}

          {canWrite && ENQUIRY_TRANSITIONS[detail.data.status].length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {ENQUIRY_TRANSITIONS[detail.data.status].map((next) => (
                <Button
                  key={next}
                  variant={next === "lost" ? "outline" : "primary"}
                  disabled={transition.pending}
                  onClick={() => void transition.mutate(next)}
                >
                  {transition.pending ? "Saving…" : `Mark ${STATUS_LABEL[next].toLowerCase()}`}
                </Button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

export function EnquiriesPage() {
  const canRead = useCan(READ, "ORG");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<EnquiryStatus | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [openId, setOpenId] = useState<string | null>(null);

  const enquiries = useAsync(
    () => listEnquiries({ q: query, ...(status ? { status } : {}), page, size }),
    [query, status, page, size],
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Enquiries"
        description="What buyers have asked for, from the storefront's enquiry form and the trade theme's line list."
      />

      {!canRead ? (
        <ErrorState
          tone="neutral"
          title="You can't see enquiries"
          message="This needs storefront.sales.enquiry.read at organization scope — held by the storefront admin role, since enquiries carry buyer contact detail."
        />
      ) : (
        <>
          {enquiries.status === "loading" ? <Skeleton className="h-64 w-full" /> : null}
          {enquiries.status === "error" ? (
            <ErrorState
              title="Couldn't load enquiries"
              message={problemMessage(enquiries.error)}
              action={<Button onClick={enquiries.reload}>Try again</Button>}
            />
          ) : null}

          {enquiries.status === "success" ? (
            <>
              <DataTable
                rows={enquiries.data.items}
                rowKey={(e) => e.id}
                toolbar={
                  <div className="flex flex-wrap items-center gap-3">
                    <SearchInput
                      label="Search enquiries"
                      placeholder="Name or company"
                      value={query}
                      onChange={(e) => {
                        setQuery(e.target.value);
                        setPage(1);
                      }}
                    />
                    <Select
                      label="Status"
                      value={status}
                      options={STATUS_OPTIONS}
                      onChange={(e) => {
                        setStatus(e.target.value as EnquiryStatus | "");
                        setPage(1);
                      }}
                      containerClassName="w-40"
                    />
                  </div>
                }
                emptyState={
                  <EmptyState
                    title={query || status ? "No enquiries match" : "No enquiries yet"}
                    description={
                      query || status
                        ? "Try clearing a filter."
                        : "Enquiries a buyer sends from the storefront's enquiry form show up here."
                    }
                    action={
                      query || status ? (
                        <Button
                          variant="outline"
                          onClick={() => {
                            setQuery("");
                            setStatus("");
                          }}
                        >
                          Clear filters
                        </Button>
                      ) : null
                    }
                  />
                }
                columns={
                  [
                    {
                      key: "name",
                      header: "From",
                      render: (e) => (
                        <div className="flex flex-col gap-0.5">
                          <span className="font-bold text-ink">{e.name}</span>
                          <span className="text-xs text-faint">{e.company}</span>
                        </div>
                      ),
                    },
                    {
                      key: "requirement",
                      header: "Requirement",
                      render: (e) => <span className="line-clamp-1 text-sm text-ink-soft">{e.requirement}</span>,
                    },
                    {
                      key: "status",
                      header: "Status",
                      render: (e) => <Tag tone={STATUS_TONE[e.status]}>{STATUS_LABEL[e.status]}</Tag>,
                    },
                    {
                      key: "created_at",
                      header: "Received",
                      cellClassName: "tnum",
                      render: (e) => new Date(e.created_at).toLocaleDateString(),
                    },
                    {
                      key: "actions",
                      header: "",
                      align: "right",
                      render: (e) => (
                        <RowActions label={e.name} actions={[{ label: "View", onSelect: () => setOpenId(e.id) }]} />
                      ),
                    },
                  ] satisfies Column<EnquiryOut>[]
                }
              />
              <Pagination
                page={page}
                pages={enquiries.data.pages}
                total={enquiries.data.total}
                size={size}
                noun="enquiries"
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

      {openId !== null ? (
        <EnquiryDetail
          id={openId}
          onClose={() => setOpenId(null)}
          onChanged={() => enquiries.reload()}
        />
      ) : null}
    </div>
  );
}
