import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, usePermission } from "@opsgrids/client";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  LoadMore,
  PageHeader,
  ReplyIcon,
  SearchInput,
  SectionHeader,
  Skeleton,
  Tag,
  formatInstant,
  useAnnounce,
} from "@opsgrids/ui";

import { listDelegations } from "@/api/approvals";
import type { DelegationCreateOut, DelegationOut } from "@/api/types";
import {
  delegationCreatedAnnouncement,
  delegationRevokedAnnouncement,
  delegationSentence,
  delegationStatusTag,
  repointedLine,
} from "@/lib/delegationCopy";
import { getMyEmployeeId } from "@/lib/subject";
import { useZoneLookup } from "@/lib/zone";
import type { ZoneDirectory } from "@/lib/zone";
import { DelegationForm } from "@/ui/DelegationForm";
import { RevokeDelegationDialog } from "@/ui/RevokeDelegationDialog";

const PAGE_SIZE = 25;

type LoadState =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error"; error: Error }
  | { status: "success" };

function DelegationRow({
  delegation,
  myEmployeeId,
  orgMode,
  zoneLookup,
  onRevoke,
}: {
  delegation: DelegationOut;
  myEmployeeId: string | null;
  orgMode: boolean;
  zoneLookup: ZoneDirectory | null;
  onRevoke: (delegation: DelegationOut) => void;
}) {
  const tag = delegationStatusTag(delegation.status);
  const range = zoneLookup
    ? `${formatInstant(delegation.starts_at, delegation.delegator_location_code, zoneLookup).text} → ${formatInstant(delegation.ends_at, delegation.delegator_location_code, zoneLookup).text}`
    : "…";
  const isDelegator = myEmployeeId !== null && delegation.delegator_employee_id === myEmployeeId;
  // §4.E: Revoke on Active/Scheduled rows only. Below ORG, only the delegator may revoke — a
  // delegate reading their own row (`GET /delegations`' OWN reading matches either party) always
  // gets 403 from `services.revoke_delegation`, so the control is never offered to them rather
  // than shown and refused (§2.4 rule 3: never a control the caller is known unable to use).
  const canRevoke =
    (delegation.status === "active" || delegation.status === "scheduled") &&
    (orgMode || isDelegator);

  return (
    <Card className="p-4 sm:p-5 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-1">
        <p className="font-semibold text-ink">{delegationSentence(delegation, myEmployeeId)}</p>
        <p className="text-sm text-muted tnum">{range}</p>
      </div>
      <div className="flex items-center gap-3">
        <Tag tone={tag.tone}>{tag.label}</Tag>
        {canRevoke ? (
          <Button variant="outline" size="sm" onClick={() => onRevoke(delegation)}>
            Revoke
          </Button>
        ) : null}
      </div>
    </Card>
  );
}

function DelegationsScreen() {
  const zoneLookup = useZoneLookup();
  const announce = useAnnounce();
  const myEmployeeId = getMyEmployeeId();
  // §2.4 rule 2: ORG is the only scope that reaches "for another employee" — TEAM and LOCATION
  // collapse to OWN (§2.12), and `usePermission` reads that ladder the same way the server does.
  const orgMode = usePermission("core.approval.delegation.write", "ORG");

  const [items, setItems] = useState<DelegationOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<DelegationOut | null>(null);
  const [repointNote, setRepointNote] = useState<string | null>(null);
  const [alreadyRevokedNote, setAlreadyRevokedNote] = useState(false);

  // §2.7: "after an action removes the element that had focus, focus moves to the nearest stable
  // heading and the change is announced". Submitting the form removes it (and the button inside
  // it) entirely; revoking removes the Revoke button from wherever the row now lives. Both move
  // focus to the section heading the affected row lands in — §4.E's own "the new row's heading" /
  // "the row's status tag in its new section", read as the nearest stable heading available.
  const activeSectionRef = useRef<HTMLDivElement>(null);
  const scheduledSectionRef = useRef<HTMLDivElement>(null);
  const pastSectionRef = useRef<HTMLDivElement>(null);
  const [pendingFocus, setPendingFocus] = useState<"active" | "scheduled" | "past" | null>(null);

  useEffect(() => {
    if (!pendingFocus) return;
    const ref =
      pendingFocus === "active"
        ? activeSectionRef
        : pendingFocus === "scheduled"
          ? scheduledSectionRef
          : pastSectionRef;
    ref.current?.focus();
    setPendingFocus(null);
  }, [pendingFocus]);

  const load = useCallback(async (pageToLoad: number, append: boolean) => {
    if (append) setLoadingMore(true);
    else setState({ status: "loading" });
    try {
      const result = await listDelegations({ page: pageToLoad, size: PAGE_SIZE });
      setItems((prev) => (append ? [...prev, ...result.items] : result.items));
      setTotal(result.total);
      setPage(pageToLoad);
      setState({ status: "success" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setState({ status: "forbidden" });
        return;
      }
      setState({ status: "error", error: err instanceof Error ? err : new Error(String(err)) });
    } finally {
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void load(1, false);
  }, [load]);

  const q = search.trim().toLowerCase();
  const filtered =
    q === ""
      ? items
      : items.filter(
          (d) =>
            (d.delegator_name?.toLowerCase().includes(q) ?? false) ||
            (d.delegate_name?.toLowerCase().includes(q) ?? false),
        );
  const hasActiveFilter = q !== "";

  const active = filtered.filter((d) => d.status === "active");
  const scheduled = filtered.filter((d) => d.status === "scheduled");
  const past = filtered.filter((d) => d.status === "ended" || d.status === "revoked");

  function replaceItem(updated: DelegationOut) {
    setItems((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
  }

  function afterCreated(
    created: DelegationCreateOut,
    forSelf: boolean,
    delegatorName: string | null,
  ) {
    setFormOpen(false);
    setItems((prev) => [created, ...prev]);
    setTotal((t) => t + 1);
    const startsImmediately = new Date(created.starts_at).getTime() <= Date.now();
    const startText = zoneLookup
      ? formatInstant(created.starts_at, created.delegator_location_code, zoneLookup).text
      : created.starts_at;
    const createdSentence = delegationCreatedAnnouncement(
      created.delegate_name,
      forSelf,
      delegatorName,
      startsImmediately,
      startText,
    );
    // §4.E's "N requests moved" line travels as part of the *same* announcement — it is stated
    // right after the create sentence, not as a second, separate live-region message — and is
    // rendered visibly too (below), with `live="off"` there so it is not spoken twice.
    const moved = repointedLine(created.delegate_name, created.repointed_count);
    announce(moved ? `${createdSentence} ${moved}` : createdSentence);
    setRepointNote(moved);
    setPendingFocus(created.status === "scheduled" ? "scheduled" : "active");
  }

  if (state.status === "forbidden") {
    return (
      <ErrorState
        title="Approvals aren't switched on here yet"
        message="Your role doesn't include approvals. An owner can check with OpsGrids support."
        tone="neutral"
      />
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Standing in for you"
        title="Delegations"
        actions={
          !formOpen ? (
            <Button variant="primary" onClick={() => setFormOpen(true)}>
              Set up a delegation
            </Button>
          ) : undefined
        }
      />

      {formOpen ? (
        <DelegationForm
          orgMode={orgMode}
          myEmployeeId={myEmployeeId}
          zoneLookup={zoneLookup}
          onCreated={afterCreated}
          onCancel={() => setFormOpen(false)}
        />
      ) : null}

      {repointNote ? (
        <InlineAlert tone="success" title={repointNote} live="off" />
      ) : null}
      {alreadyRevokedNote ? (
        <InlineAlert tone="warning" title="This was already revoked." live="auto" />
      ) : null}

      {items.length > 0 ? (
        <Card className="p-4 sm:p-5">
          <SearchInput
            label="Search delegations"
            placeholder="Search by person…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
        </Card>
      ) : null}

      {state.status === "loading" ? (
        <Card className="p-6">
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-tile" />
            ))}
          </div>
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} onRetry={() => void load(1, false)} />
        </Card>
      ) : null}

      {state.status === "success" ? (
        items.length === 0 ? (
          <EmptyState
            icon={<ReplyIcon className="size-6" />}
            title="No delegations yet"
            description="Going away? Let a colleague decide your approvals while you're out."
            action={
              !formOpen ? (
                <Button variant="primary" onClick={() => setFormOpen(true)}>
                  Set up a delegation
                </Button>
              ) : undefined
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No delegations match this search"
            description="Try another search."
            action={
              <Button variant="outline" onClick={() => setSearch("")}>
                Clear search
              </Button>
            }
          />
        ) : (
          <div className="flex flex-col gap-6">
            {active.length > 0 ? (
              <div ref={activeSectionRef} tabIndex={-1} className="flex flex-col gap-3 focus:outline-none">
                <SectionHeader title="Active" />
                {active.map((d) => (
                  <DelegationRow
                    key={d.id}
                    delegation={d}
                    myEmployeeId={myEmployeeId}
                    orgMode={orgMode}
                    zoneLookup={zoneLookup}
                    onRevoke={setRevokeTarget}
                  />
                ))}
              </div>
            ) : null}
            {scheduled.length > 0 ? (
              <div ref={scheduledSectionRef} tabIndex={-1} className="flex flex-col gap-3 focus:outline-none">
                <SectionHeader title="Scheduled" />
                {scheduled.map((d) => (
                  <DelegationRow
                    key={d.id}
                    delegation={d}
                    myEmployeeId={myEmployeeId}
                    orgMode={orgMode}
                    zoneLookup={zoneLookup}
                    onRevoke={setRevokeTarget}
                  />
                ))}
              </div>
            ) : null}
            {past.length > 0 ? (
              <div ref={pastSectionRef} tabIndex={-1} className="flex flex-col gap-3 focus:outline-none">
                <SectionHeader title="Past" />
                {past.map((d) => (
                  <DelegationRow
                    key={d.id}
                    delegation={d}
                    myEmployeeId={myEmployeeId}
                    orgMode={orgMode}
                    zoneLookup={zoneLookup}
                    onRevoke={setRevokeTarget}
                  />
                ))}
              </div>
            ) : null}

            {hasActiveFilter && items.length < total ? (
              <p className="text-center text-sm text-muted">
                Search only looks at what's loaded so far. Clear it to load more delegations.
              </p>
            ) : (
              <LoadMore
                loaded={items.length}
                total={total}
                noun="delegations"
                loading={loadingMore}
                onLoadMore={() => void load(page + 1, true)}
              />
            )}
          </div>
        )
      ) : null}

      {revokeTarget ? (
        <RevokeDelegationDialog
          open
          onClose={() => setRevokeTarget(null)}
          delegation={revokeTarget}
          myEmployeeId={myEmployeeId}
          onRevoked={(revoked) => {
            setRevokeTarget(null);
            replaceItem(revoked);
            const forSelf = myEmployeeId !== null && revoked.delegator_employee_id === myEmployeeId;
            announce(
              delegationRevokedAnnouncement(revoked.delegate_name, forSelf, revoked.delegator_name),
            );
            setPendingFocus("past");
          }}
          onAlreadyRevoked={() => {
            setRevokeTarget(null);
            setAlreadyRevokedNote(true);
            void load(1, false);
          }}
        />
      ) : null}
    </div>
  );
}

export function DelegationsPage() {
  return (
    <Announcer>
      <DelegationsScreen />
    </Announcer>
  );
}
