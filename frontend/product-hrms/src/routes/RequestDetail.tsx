import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import {
  Announcer,
  ArrowLeftIcon,
  Button,
  Card,
  ErrorState,
  InlineAlert,
  PageHeader,
  SectionHeader,
  Skeleton,
  Tag,
  Timeline,
  formatInstant,
  useAnnounce,
} from "@opsgrids/ui";

import { getRequestDetail } from "@/api/approvals";
import type { RequestDetailOut } from "@/api/types";
import {
  applicationOutcomeCopy,
  buildHistoryEntries,
  detailStatusCopy,
  inboxReasonChip,
} from "@/lib/approvalCopy";
import { attendanceStatusLabel } from "@/lib/format";
import { getMyEmployeeId } from "@/lib/subject";
import { useZoneLookup } from "@/lib/zone";
import { DecisionDialog } from "@/ui/DecisionDialog";
import { PersonRefBlock } from "@/ui/PersonRef";

type Status =
  | { kind: "loading" }
  | { kind: "not-found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; detail: RequestDetailOut };

function RequestDetailScreen() {
  const { requestId } = useParams<{ requestId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const zoneLookup = useZoneLookup();
  const announce = useAnnounce();
  const myEmployeeId = getMyEmployeeId();

  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [decisionOutcome, setDecisionOutcome] = useState<"approve" | "reject" | null>(null);
  const [conflictComment, setConflictComment] = useState<string | null>(null);
  const [ineligible, setIneligible] = useState<string | null>(null);
  const statusPillRef = useRef<HTMLSpanElement>(null);
  const conflictRef = useRef<HTMLDivElement>(null);

  const cameFromMine = (location.state as { from?: string } | null)?.from === "mine";

  const load = useCallback(
    (opts: { silent?: boolean } = {}) => {
      if (!requestId) return;
      // A silent reload (after a 409) keeps the current screen up so the conflict message it is
      // about to show has something to sit next to — a full-page skeleton would wipe it.
      if (!opts.silent) setStatus({ kind: "loading" });
      getRequestDetail(requestId)
        .then((detail) => setStatus({ kind: "ready", detail }))
        .catch((err: unknown) => {
          if (err instanceof ApiError && err.status === 404) {
            setStatus({ kind: "not-found" });
            return;
          }
          setStatus({
            kind: "error",
            message: err instanceof Error ? err.message : "Something went wrong",
          });
        });
    },
    [requestId],
  );

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  useEffect(() => {
    if (conflictComment !== null) conflictRef.current?.focus();
  }, [conflictComment]);

  if (status.kind === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-80" />
        <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
          <Card className="p-6 flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </Card>
          <Card className="p-6 flex flex-col gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </Card>
        </div>
      </div>
    );
  }

  if (status.kind === "not-found") {
    return (
      <ErrorState
        title="That request isn't available to you"
        message="It may have been removed, or it may belong to someone else's chain."
        tone="neutral"
        action={
          <Button variant="dark" onClick={() => navigate("/approvals")}>
            Back to approvals
          </Button>
        }
      />
    );
  }

  if (status.kind === "error") {
    return <ErrorState message={status.message} onRetry={() => load()} />;
  }

  const { detail } = status;
  const formatAt = (iso: string) => {
    const f = formatInstant(iso, detail.requester_location_code, zoneLookup ?? { locations: {}, organizationZone: "UTC" });
    return { text: f.text, dateTime: f.dateTime };
  };

  const pill = detailStatusCopy(detail.status, detail.application_state, detail.approver_name);
  const isRequester = myEmployeeId !== null && detail.requester_employee_id === myEmployeeId;
  const canDecide = detail.status === "pending" && detail.inbox_reason !== null;
  const outcomeCopy =
    detail.application_state === "failed"
      ? applicationOutcomeCopy(detail.status, detail.application_failure_reason)
      : null;
  const chip = inboxReasonChip(detail.inbox_reason, detail.from_name);
  const historyEntries = buildHistoryEntries(detail, formatAt);
  const requesterDay = attendanceStatusLabel(detail.summary.from);
  const requesterTargetDay = attendanceStatusLabel(detail.summary.to);
  const dateLabel = formatInstant(`${detail.summary.date}T00:00:00Z`, detail.requester_location_code, zoneLookup ?? { locations: {}, organizationZone: "UTC" }, "date").text;
  const raised = formatAt(detail.opened_at);

  function afterDecision(next: RequestDetailOut) {
    setDecisionOutcome(null);
    setStatus({ kind: "ready", detail: next });
    const message =
      next.status === "approved"
        ? `Approved ${next.requester_name ?? "this employee"}'s attendance correction for ${dateLabel}.`
        : `Rejected ${next.requester_name ?? "this employee"}'s attendance correction for ${dateLabel}.`;
    if (cameFromMine) {
      // An ORG reader deciding a request opened from "My requests" stays on the detail — the
      // requester is never a decider, so this only happens for them (§1.4).
      announce(message);
      statusPillRef.current?.focus();
    } else {
      navigate("/approvals", { state: { announce: message } });
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to={cameFromMine ? "/approvals/mine" : "/approvals"}
        className="inline-flex items-center gap-2 text-sm font-semibold text-muted hover:text-ink w-fit"
      >
        <ArrowLeftIcon className="size-4" />
        {cameFromMine ? "Back to my requests" : "Back to approvals"}
      </Link>

      <PageHeader
        eyebrow="Attendance correction"
        title={detail.requester_name ?? "Employee record unavailable"}
        actions={
          <span
            ref={statusPillRef}
            tabIndex={-1}
            className="focus:outline-none"
          >
            <Tag tone={pill.tone}>{pill.label}</Tag>
          </span>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6 items-start">
        <div className="flex flex-col gap-6">
          <Card className="p-6">
            <SectionHeader title="What's being asked" />
            <dl className="mt-4 flex flex-col gap-3 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Employee</dt>
                <dd>
                  <PersonRefBlock
                    name={detail.requester_name}
                    employeeNo={detail.requester_employee_no}
                  />
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Date</dt>
                <dd className="text-ink font-semibold tnum">{dateLabel}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Change</dt>
                <dd className="flex items-center gap-2">
                  <span className="sr-only">{`from ${requesterDay} to ${requesterTargetDay}`}</span>
                  <span aria-hidden className="flex items-center gap-2">
                    <Tag>{requesterDay}</Tag>
                    <span aria-hidden>→</span>
                    <Tag>{requesterTargetDay}</Tag>
                  </span>
                </dd>
              </div>
              {detail.request_note ? (
                <div className="flex flex-col gap-1 border-t border-line pt-3">
                  <dt className="text-muted">Reason</dt>
                  <dd className="whitespace-pre-wrap text-ink-soft">{detail.request_note}</dd>
                </div>
              ) : null}
              <div className="flex justify-between gap-4 border-t border-line pt-3">
                <dt className="text-muted">Raised</dt>
                <dd className="text-ink-soft" title={raised.dateTime}>
                  {raised.text}
                </dd>
              </div>
            </dl>
          </Card>

          {canDecide ? (
            <Card className="p-6">
              <SectionHeader title="Your decision" />
              <div className="mt-4 flex flex-col gap-3 sm:flex-row">
                <Button variant="primary" onClick={() => setDecisionOutcome("approve")}>
                  Approve
                </Button>
                <Button variant="outline" onClick={() => setDecisionOutcome("reject")}>
                  Reject
                </Button>
              </div>
              {detail.from_name && detail.inbox_reason?.startsWith("delegated") ? (
                <p className="mt-3 text-sm text-muted">You're deciding for {detail.from_name}.</p>
              ) : null}
            </Card>
          ) : detail.status === "pending" ? (
            <Card className="p-6">
              <SectionHeader title="Your decision" />
              <p className="mt-3 text-sm text-muted">
                {isRequester
                  ? "You can't decide your own request."
                  : detail.approver_name
                    ? // §4.B's "no permission (decide)" state adds the direct-override sentence
                      // only "for an ORG reader looking at an assigned request" — i.e. someone
                      // holding `hrms.time.attendance.write:ORG`. There is no client-side grants
                      // source to test that (§2.4 gap G1: `MeOut` carries `roles`, not grants),
                      // so asserting the sentence to every reader who reaches this branch would
                      // promise an action a TEAM/LOCATION/OWN reader may not have. Left out until
                      // there is a real signal to gate it on; see the report for how to wire it.
                      `Only ${detail.approver_name} can decide this.`
                    : // §2.3's own approved copy for the unassigned case — not invented text.
                      "Unassigned — no approver found."}
              </p>
            </Card>
          ) : null}

          {conflictComment !== null ? (
            <div ref={conflictRef} tabIndex={-1} className="focus:outline-none">
              <InlineAlert tone="warning" title="Already decided" live="auto">
                <p>
                  {detail.decided_by_name ?? "Someone"}{" "}
                  {detail.status === "rejected" ? "rejected" : "approved"} this on{" "}
                  {formatAt(detail.decided_at ?? detail.opened_at).text}.
                </p>
                {conflictComment ? <p>Your comment wasn't saved.</p> : null}
              </InlineAlert>
            </div>
          ) : null}

          {ineligible !== null ? (
            <InlineAlert tone="warning" title="You can no longer decide this request.">
              {ineligible}
            </InlineAlert>
          ) : null}

          {outcomeCopy ? (
            <InlineAlert tone="warning" title={outcomeCopy.title}>
              <p>{outcomeCopy.body}</p>
              <p>{outcomeCopy.instruction}</p>
            </InlineAlert>
          ) : null}
        </div>

        <div className="flex flex-col gap-6">
          {detail.status === "pending" ? (
            <Card className="p-6">
              <SectionHeader title="Waiting on" />
              <div className="mt-3 flex flex-col gap-2">
                {detail.approver_name || detail.approver_employee_id ? (
                  <PersonRefBlock name={detail.approver_name} employeeNo={null} />
                ) : (
                  <p className="text-sm text-muted">Unassigned — no approver found</p>
                )}
                {chip ? <Tag tone={chip.tone}>{chip.label}</Tag> : null}
              </div>
            </Card>
          ) : null}

          <Card className="p-6">
            <SectionHeader title="History" />
            <div className="mt-4">
              <Timeline
                entries={historyEntries.map((e) => ({
                  id: e.id,
                  tone: e.tone,
                  content: e.content,
                  ...(e.quote ? { quote: e.quote } : {}),
                  timestamp: e.timestamp,
                  dateTime: e.dateTime,
                }))}
                collapseAfter={6}
              />
            </div>
          </Card>
        </div>
      </div>

      {decisionOutcome ? (
        <DecisionDialog
          open={decisionOutcome !== null}
          onClose={() => setDecisionOutcome(null)}
          outcome={decisionOutcome}
          request={detail}
          zoneLookup={zoneLookup}
          onDecided={afterDecision}
          onConflict={(typedComment) => {
            setDecisionOutcome(null);
            setConflictComment(typedComment);
            load({ silent: true });
          }}
          onIneligible={(detailMessage) => {
            setDecisionOutcome(null);
            setIneligible(detailMessage);
          }}
        />
      ) : null}
    </div>
  );
}

export function RequestDetailPage() {
  return (
    <Announcer>
      <RequestDetailScreen />
    </Announcer>
  );
}
