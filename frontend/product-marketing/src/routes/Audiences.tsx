import { useState } from "react";
import { Link } from "react-router-dom";

import { deleteAudience, getAudiences, getCampaigns } from "@/api/client";
import type { Audience, Campaign } from "@/api/types";
import { campaignsForAudience } from "@/data/derive";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatMoneyCompact, formatNumber } from "@/lib/format";
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Pagination,
  PageHeader,
  RowActions,
  SearchInput,
  Skeleton,
} from "@/ui";
import { ArrowRightIcon, PlusIcon, UsersIcon, useToast } from "@opsgrids/ui";
import { AudienceFormDialog } from "@/forms/AudienceFormDialog";

const WRITE_PERMISSION = "marketing.analytics.campaign.write";

/**
 * Every campaign in the org — used only to compute "used by N campaigns" and the revenue each
 * audience influenced. A bounded working set is enough for that cross-reference; the audiences
 * list itself is server-paged independently.
 */
const CAMPAIGN_PROBE_SIZE = 200;

export function AudiencesPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Audience | null>(null);
  const [deleting, setDeleting] = useState<Audience | null>(null);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");

  const audiencesState = useAsync(
    () => getAudiences({ q: debouncedSearch, page, size }),
    [debouncedSearch, page, size],
  );
  // A separate, unfiltered load: "used by" has to count against every campaign, not just the
  // audiences page currently on screen.
  const campaignsState = useAsync(() => getCampaigns({ size: CAMPAIGN_PROBE_SIZE }), []);
  const campaigns: Campaign[] = campaignsState.status === "success" ? campaignsState.data.items : [];

  const remove = useMutation((a: Audience) => deleteAudience(a.id), {
    onSuccess: (_result, a) => {
      setDeleting(null);
      toast.success(`${a.name} deleted.`);
      audiencesState.reload();
    },
  });

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Reach"
        title="Audiences"
        actions={
          <Can permission={WRITE_PERMISSION} scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New audience
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-5">
        <SearchInput
          label="Search audiences"
          placeholder="Search by name or criteria…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          containerClassName="sm:max-w-sm"
        />
      </Card>

      {audiencesState.status === "loading" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="p-6 flex flex-col gap-4">
              <div className="flex gap-3">
                <Skeleton className="size-11 rounded-tile" />
                <div className="flex-1 flex flex-col gap-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-20" />
                </div>
              </div>
              <Skeleton className="h-10 w-full rounded-tile" />
              <Skeleton className="h-9 w-40 rounded-full" />
            </Card>
          ))}
        </div>
      ) : null}

      {audiencesState.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load audiences"
            message={problemMessage(audiencesState.error)}
            onRetry={audiencesState.reload}
          />
        </Card>
      ) : null}

      {audiencesState.status === "success" ? (
        audiencesState.data.items.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title={debouncedSearch ? "No audiences match that search" : "No audiences yet"}
              description={
                debouncedSearch
                  ? "No segment matches that search. Try a different name or criteria keyword."
                  : "Create the first segment so a campaign has somewhere to target."
              }
              action={
                debouncedSearch ? null : (
                  <Can permission={WRITE_PERMISSION} scope="ORG">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      New audience
                    </Button>
                  </Can>
                )
              }
            />
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {audiencesState.data.items.map((audience) => {
                const used = campaignsForAudience(campaigns, audience.id);
                const influencedRevenue = used.reduce((sum, c) => sum + c.revenue_minor, 0);
                const currency = used[0]?.currency ?? "INR";
                return (
                  <Card key={audience.id} className="flex flex-col gap-4 p-6">
                    <div className="flex items-start gap-3">
                      <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                        <UsersIcon className="size-5" />
                      </span>
                      <div className="flex min-w-0 flex-1 flex-col leading-tight">
                        <span className="font-bold text-ink">{audience.name}</span>
                        <span className="text-sm text-muted tnum">
                          {formatNumber(audience.size)} reachable people
                        </span>
                      </div>
                      <RowActions
                        label={audience.name}
                        actions={[
                          { label: "Edit", onSelect: () => setEditing(audience), hidden: !canWrite },
                          {
                            label: "Delete",
                            tone: "danger",
                            onSelect: () => setDeleting(audience),
                            hidden: !canWrite,
                            disabled: used.length > 0,
                            disabledReason: "Targeted by a campaign — retarget or delete it first.",
                          },
                        ]}
                      />
                    </div>

                    <p className="min-h-10 text-sm text-ink-soft">
                      {audience.criteria || "No criteria on file."}
                    </p>

                    <div className="grid grid-cols-2 gap-2 border-t border-line pt-4">
                      <div className="flex flex-col">
                        <span className="display text-lg font-extrabold tnum">{used.length}</span>
                        <span className="text-xs text-muted">
                          {used.length === 1 ? "campaign" : "campaigns"}
                        </span>
                      </div>
                      <div className="flex flex-col">
                        <span className="display text-lg font-extrabold tnum">
                          {formatMoneyCompact(influencedRevenue, currency)}
                        </span>
                        <span className="text-xs text-muted">revenue influenced</span>
                      </div>
                    </div>

                    {used.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {used.slice(0, 3).map((c) => (
                          <Link
                            key={c.id}
                            to={`/campaigns/${c.id}`}
                            className="inline-flex max-w-full items-center gap-1 rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-ink-soft hover:bg-accent-soft hover:text-accent-ink transition-colors"
                          >
                            <span className="truncate">{c.name}</span>
                            <ArrowRightIcon className="size-3 shrink-0" />
                          </Link>
                        ))}
                        {used.length > 3 ? (
                          <span className="inline-flex items-center rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-faint">
                            +{used.length - 3} more
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      <span className="text-xs text-faint">Not targeted by any campaign yet.</span>
                    )}
                  </Card>
                );
              })}
            </div>
            <Pagination
              page={audiencesState.data.page}
              pages={audiencesState.data.pages}
              total={audiencesState.data.total}
              size={audiencesState.data.size}
              noun="audiences"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        )
      ) : null}

      {creating ? (
        <AudienceFormDialog
          audience={null}
          onClose={() => setCreating(false)}
          onSaved={(saved) => {
            setCreating(false);
            toast.success(`${saved.name} created.`);
            audiencesState.reload();
          }}
        />
      ) : null}

      {editing ? (
        <AudienceFormDialog
          audience={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast.success(`${saved.name} saved.`);
            audiencesState.reload();
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
        title="Delete this audience?"
        body={`${deleting?.name} is removed from Audiences. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
