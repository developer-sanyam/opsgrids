import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  BuildingUserIcon,
  Card,
  ErrorState,
  InlineAlert,
  MapPinIcon,
  PageHeader,
  SectionHeader,
  Skeleton,
  UsersIcon,
} from "@opsgrids/ui";

import { getEmployee } from "@/api/client";
import { getChain } from "@/api/approvals";
import type { ApprovalChainOut, ChainRungOut, EmployeeOut } from "@/api/types";
import { departmentLabel } from "@/data/departments";
import { employmentLabel } from "@/lib/format";
import { EmployeeStatusPill } from "@/ui";
import { PersonRefBlock } from "@/ui/PersonRef";

/** §4.F, "Loaded, large": beyond this many rungs the chain collapses to the first 3 and last 2. */
const CHAIN_COLLAPSE_THRESHOLD = 8;
const CHAIN_VISIBLE_HEAD = 3;
const CHAIN_VISIBLE_TAIL = 2;

type EmployeeState =
  | { kind: "loading" }
  | { kind: "not-found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; employee: EmployeeOut };

type ChainState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; chain: ApprovalChainOut };

function ChainRung({ rung }: { rung: ChainRungOut }) {
  return (
    <li
      data-rung-level={rung.level}
      tabIndex={-1}
      className="flex flex-col gap-1 focus:outline-none"
    >
      <div className="flex items-center gap-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-ground text-sm font-bold text-ink-soft tnum">
          {rung.level}
        </span>
        {rung.is_fallback ? (
          <div className="flex items-center gap-3">
            <span className="grid size-8 place-items-center rounded-full bg-ground text-muted">
              <BuildingUserIcon className="size-4" />
            </span>
            <div className="flex flex-col leading-tight">
              <span className="font-semibold text-ink">HR</span>
              <span className="text-xs text-muted">
                If nobody above can decide, the request goes to HR.
              </span>
            </div>
          </div>
        ) : (
          <PersonRefBlock name={rung.display_name} employeeNo={rung.employee_no} />
        )}
      </div>
      {rung.skipped ? (
        <p className="ml-10 text-xs text-muted">
          Skipped {rung.display_name ?? "Employee record unavailable"} — no longer with the company.
        </p>
      ) : null}
    </li>
  );
}

function ApprovalChainSection({ employeeId, firstName }: { employeeId: string; firstName: string }) {
  const [state, setState] = useState<ChainState>({ kind: "loading" });
  const [expanded, setExpanded] = useState(false);
  const chainContainerRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setState({ kind: "loading" });
    getChain(employeeId)
      .then((chain) => setState({ kind: "ready", chain }))
      .catch((err: unknown) =>
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Something went wrong",
        }),
      );
  }, [employeeId]);

  useEffect(() => {
    load();
  }, [load]);

  // A fresh employee's chain starts collapsed again — `expanded` describes the chain currently
  // loaded, not a standing preference.
  useEffect(() => {
    setExpanded(false);
  }, [employeeId]);

  const rungs = state.kind === "ready" ? state.chain.chain : [];
  const shouldCollapse = rungs.length > CHAIN_COLLAPSE_THRESHOLD && !expanded;
  const firstRevealedLevel = shouldCollapse ? undefined : rungs[CHAIN_VISIBLE_HEAD]?.level;

  useEffect(() => {
    if (expanded && firstRevealedLevel !== undefined) {
      chainContainerRef.current
        ?.querySelector<HTMLElement>(`[data-rung-level="${firstRevealedLevel}"]`)
        ?.focus();
    }
    // Fires only on the transition into `expanded` — see the guard above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  return (
    <Card className="p-6">
      <SectionHeader
        title="Approval chain"
        subtitle={`Who decides ${firstName}'s requests, in order.`}
      />
      <div className="mt-4">
        {state.kind === "loading" ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}

        {state.kind === "error" ? (
          <ErrorState message={state.message} onRetry={load} />
        ) : null}

        {state.kind === "ready" ? (
          <div className="flex flex-col gap-4">
            {state.chain.unresolved ? (
              <InlineAlert tone="warning" title="This chain couldn't be worked out — the reporting line loops back on itself.">
                Requests from {firstName} go to HR until it's fixed.
              </InlineAlert>
            ) : null}

            {state.chain.chain.length === 1 && state.chain.chain[0]?.is_fallback ? (
              <p className="text-sm text-muted">
                {firstName} has no reporting manager, so their requests go straight to HR.
              </p>
            ) : null}

            <div ref={chainContainerRef} className="flex flex-col gap-3">
              {shouldCollapse ? (
                <>
                  <ol className="flex flex-col gap-3">
                    {rungs.slice(0, CHAIN_VISIBLE_HEAD).map((rung) => (
                      <ChainRung key={rung.level} rung={rung} />
                    ))}
                  </ol>
                  {/* §4.F: "Loaded, large" — beyond 8 rungs the chain collapses to the first 3
                   *  and last 2, behind a control naming how many levels are hidden. The HR
                   *  fallback is never collapsed: it is always the chain's own last rung, so it
                   *  is always inside this trailing slice. */}
                  <button
                    type="button"
                    aria-expanded={false}
                    onClick={() => setExpanded(true)}
                    className="ml-10 self-start text-sm font-semibold text-accent hover:underline"
                  >
                    Show all {rungs.length} levels
                  </button>
                  <ol className="flex flex-col gap-3">
                    {rungs.slice(-CHAIN_VISIBLE_TAIL).map((rung) => (
                      <ChainRung key={rung.level} rung={rung} />
                    ))}
                  </ol>
                </>
              ) : (
                <ol className="flex flex-col gap-3">
                  {rungs.map((rung) => (
                    <ChainRung key={rung.level} rung={rung} />
                  ))}
                </ol>
              )}
            </div>

            {state.chain.dotted_line_manager ? (
              <div className="border-t border-line pt-4">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">
                  Dotted-line manager
                </h3>
                <div className="mt-2 flex items-center gap-3">
                  <PersonRefBlock
                    name={state.chain.dotted_line_manager.display_name}
                    employeeNo={state.chain.dotted_line_manager.employee_no}
                  />
                </div>
                <p className="mt-1 text-xs text-muted">
                  Not used for approvals. Requests follow the reporting line above.
                </p>
              </div>
            ) : null}

            <p className="text-xs text-muted">
              Requests keep the approver they were sent to, even if this chain changes later.
            </p>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export function EmployeeDetailPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const [state, setState] = useState<EmployeeState>({ kind: "loading" });

  useEffect(() => {
    if (!employeeId) return;
    setState({ kind: "loading" });
    getEmployee(employeeId)
      .then((employee) => setState({ kind: "ready", employee }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          setState({ kind: "not-found" });
          return;
        }
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Something went wrong",
        });
      });
  }, [employeeId]);

  if (state.kind === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-40 w-full rounded-card" />
      </div>
    );
  }

  if (state.kind === "not-found") {
    return (
      <ErrorState
        title="That employee isn't available to you"
        message="It may have been removed, or you may not have access."
        tone="neutral"
      />
    );
  }

  if (state.kind === "error") {
    return <ErrorState message={state.message} />;
  }

  const { employee } = state;
  const firstName = employee.full_name.split(" ")[0] ?? employee.full_name;

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/employees"
        className="inline-flex items-center gap-2 text-sm font-semibold text-muted hover:text-ink w-fit"
      >
        <ArrowLeftIcon className="size-4" />
        Back to employees
      </Link>

      <PageHeader
        {...(employee.designation ? { eyebrow: employee.designation } : {})}
        title={employee.full_name}
        actions={<EmployeeStatusPill status={employee.status} />}
      />

      <Card className="p-6">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Employee number
            </span>
            <span className="tnum text-ink">{employee.employee_no}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Department
            </span>
            <span className="inline-flex items-center gap-1.5 text-ink">
              <UsersIcon className="size-4 text-faint" />
              {departmentLabel(employee)}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Location
            </span>
            <span className="inline-flex items-center gap-1.5 text-ink">
              <MapPinIcon className="size-4 text-faint" />
              {employee.location_code ?? "—"}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Employment
            </span>
            <span className="text-ink">
              {employmentLabel(employee.employment_type)} · {employee.work_mode}
            </span>
          </div>
        </div>
      </Card>

      <ApprovalChainSection employeeId={employee.id} firstName={firstName} />
    </div>
  );
}
