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
  SectionHeader,
  Skeleton,
  Tag,
  formatInstant,
  useAnnounce,
} from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

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
import { DelegatePicker } from "@/ui/DelegatePicker";
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
  // §4.E "filtered to a person": the server does this filtering (`employee_id`, either
  // direction), so `total` and "Load more" keep describing the same set the list is showing. The
  // screen never filters loaded rows itself — that was the paging lie this replaced.
  const [person, setPerson] = useState<ComboboxOption | null>(null);
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

  const load = useCallback(async (pageToLoad: number, append: boolean, employeeId: string | null) => {
    if (append) setLoadingMore(true);
    else setState({ status: "loading" });
    try {
      const result = await listDelegations({
        page: pageToLoad,
        size: PAGE_SIZE,
        ...(employeeId !== null ? { employeeId } : {}),
      });
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

  const personId = person?.id ?? null;

  // Changing the person resets to page 1 and drops the accumulated pages (§2.6) — the new filter
  // is a different query, not a view over the old one's rows.
  useEffect(() => {
    void load(1, false, personId);
  }, [load, personId]);

  const active = items.filter((d) => d.status === "active");
  const scheduled = items.filter((d) => d.status === "scheduled");
  const past = items.filter((d) => d.status === "ended" || d.status === "revoked");

  function replaceItem(updated: DelegationOut) {
    setItems((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
  }

  function afterCreated(
    created: DelegationCreateOut,
    forSelf: boolean,
    delegatorName: string | null,
  ) {
    setFormOpen(false);
    // A person filter is a server-side query, so a new row only belongs in the list when the
    // server would have returned it. When it would not, the list is reloaded rather than guessed
    // at — prepending a row the filter excludes would put `total` and the visible rows back out
    // of step, which is the thing this screen just stopped doing.
    const matchesFilter =
      personId === null ||
      created.delegator_employee_id === personId ||
      created.delegate_employee_id === personId;
    if (matchesFilter) {
      setItems((prev) => [created, ...prev]);
      setTotal((t) => t + 1);
    } else {
      void load(1, false, personId);
    }
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

      {/* §4.E's person filter, served by the same `/delegate-candidates` combobox the form uses —
       *  one way to look a colleague up on this screen, not two. `employee_id` is ANDed with the
       *  caller's own scope, so below ORG it narrows "rows I'm a party to" rather than the whole
       *  organization, and an id matching nobody in that intersection is an ordinary empty result,
       *  never an error. The control stays mounted while a filter is applied even when the result
       *  is empty — otherwise clearing it would need the control the empty result just removed. */}
      {items.length > 0 || personId !== null ? (
        <Card className="p-4 sm:p-5">
          <div className="lg:max-w-xs">
            <DelegatePicker
              label="Filter by person"
              value={person}
              onChange={setPerson}
              helper="Shows delegations they hand over and delegations they take on."
            />
          </div>
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
          <ErrorState message={state.error.message} onRetry={() => void load(1, false, personId)} />
        </Card>
      ) : null}

      {state.status === "success" ? (
        items.length === 0 ? (
          person ? (
            // §4.E "Empty (ORG mode, filtered to a person)" — a true statement about that person
            // now that the server, not the browser, decided the set. Below ORG the same query is
            // ANDed with the caller's own rows, so the honest claim is narrower: the *intersection*
            // is empty, not that person's whole history, and the title says so. Clearing the filter
            // is the picker's own removable chip, still on screen above.
            <EmptyState
              icon={<ReplyIcon className="size-6" />}
              title={
                orgMode
                  ? `${person.label} has no delegations`
                  : `You have no delegations with ${person.label}`
              }
              description="You can set one up for them."
            />
          ) : (
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
          )
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

            {/* Both numbers now describe the same, server-decided set, filtered or not — so
             *  "Load more · {n} left" is a promise the next page keeps (§2.6). */}
            <LoadMore
              loaded={items.length}
              total={total}
              noun="delegations"
              loading={loadingMore}
              onLoadMore={() => void load(page + 1, true, personId)}
            />
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
            void load(1, false, personId);
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
