import { useState } from "react";

import { ApiError, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  ConfirmDialog,
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
  StatusBadge,
  statusTone,
  useDebouncedValue,
  useToast,
} from "@opsgrids/ui";
import type { Column, StatusTone, TableSelection } from "@opsgrids/ui";

import { downloadCsv } from "@/lib/csv";
import { deleteEnquiry, getEnquiry, listEnquiries, updateEnquiryStatus } from "./api";
import { ENQUIRY_TRANSITIONS } from "./types";
import type { EnquiryOut, EnquiryStatus } from "./types";

const WRITE = "storefront.sales.enquiry.write";
const READ = "storefront.sales.enquiry.read";
const DELETE = "storefront.sales.enquiry.delete";

const STATUS_OPTIONS = [
  { value: "", label: "Any status" },
  { value: "new", label: "New" },
  { value: "contacted", label: "Contacted" },
  { value: "won", label: "Won" },
  { value: "lost", label: "Lost" },
];

/**
 * `new` and `won` are registered words the shared registry already tones the way this app always
 * has. `contacted` isn't a word the registry knows, and `lost` means something different here than
 * the registry's general sense of it (see `opsgrids-ui/src/lib/status.ts`) — a lost enquiry is a
 * closed-out fact, not the failure `lost` reads as for a sales deal — so both take an explicit
 * tone instead.
 */
const STATUS_TONE_OVERRIDE: Partial<Record<EnquiryStatus, StatusTone>> = {
  contacted: "warning",
  lost: "neutral",
};

const STATUS_LABEL: Record<EnquiryStatus, string> = {
  new: "New",
  contacted: "Contacted",
  won: "Won",
  lost: "Lost",
};

function enquiryTone(status: EnquiryStatus): StatusTone {
  return STATUS_TONE_OVERRIDE[status] ?? statusTone(status) ?? "neutral";
}

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
            <StatusBadge tone={enquiryTone(detail.data.status)} label={STATUS_LABEL[detail.data.status]} />
          </div>

          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
  const toast = useToast();
  const canRead = useCan(READ, "ORG");
  const canDelete = useCan(DELETE, "ORG");
  const [deleting, setDeleting] = useState<EnquiryOut | null>(null);
  const [rawQuery, setRawQuery] = useState("");
  const query = useDebouncedValue(rawQuery);
  const [status, setStatus] = useState<EnquiryStatus | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [openId, setOpenId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const enquiries = useAsync(
    () => listEnquiries({ q: query, ...(status ? { status } : {}), page, size }),
    [query, status, page, size],
  );

  const enquirySelection: TableSelection<EnquiryOut> | undefined =
    enquiries.status === "success"
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
            setSelected(next ? new Set(enquiries.data.items.map((e) => e.id)) : new Set()),
          label: (e) => e.name,
        }
      : undefined;

  const erase = useMutation((enquiry: EnquiryOut) => deleteEnquiry(enquiry.id), {
    onSuccess: (_result, enquiry) => {
      setDeleting(null);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(enquiry.id);
        return next;
      });
      toast.success(`Enquiry from ${enquiry.name} deleted.`);
      enquiries.reload();
    },
  });

  function exportSelectedEnquiries() {
    if (enquiries.status !== "success") return;
    const chosen = enquiries.data.items.filter((e) => selected.has(e.id));
    downloadCsv(
      "enquiries.csv",
      ["From", "Company", "Requirement", "Status", "Received"],
      chosen.map((e) => [e.name, e.company, e.requirement, STATUS_LABEL[e.status], new Date(e.created_at).toLocaleDateString()]),
    );
  }

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
                mobileCard="auto"
                selection={enquirySelection}
                bulkActions={
                  <Button variant="outline" size="sm" onClick={exportSelectedEnquiries}>
                    Export selected
                  </Button>
                }
                toolbar={
                  <div className="flex flex-wrap items-center gap-3">
                    <SearchInput
                      label="Search enquiries"
                      placeholder="Name or company"
                      value={rawQuery}
                      onChange={(e) => {
                        setRawQuery(e.target.value);
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
                            setRawQuery("");
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
                      mobile: "title",
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
                      mobile: "status",
                      render: (e) => <StatusBadge tone={enquiryTone(e.status)} label={STATUS_LABEL[e.status]} />,
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
                      mobile: "actions",
                      render: (e) => (
                        <RowActions
                          label={e.name}
                          actions={[
                            { label: "View", onSelect: () => setOpenId(e.id) },
                            { label: "Delete", tone: "danger", onSelect: () => setDeleting(e), hidden: !canDelete },
                          ]}
                        />
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

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          erase.reset();
        }}
        onConfirm={() => deleting && void erase.mutate(deleting)}
        title="Delete this enquiry?"
        body={`The enquiry from ${deleting?.name}, with their email, phone and any WhatsApp opt-in it holds, is erased. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={erase.pending}
        {...(erase.error instanceof ApiError ? { error: erase.error.message } : {})}
      />
    </div>
  );
}
