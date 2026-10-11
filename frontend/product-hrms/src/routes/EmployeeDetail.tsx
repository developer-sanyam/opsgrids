import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, revealRequest, useCan, useRevealedField, useStepUp } from "@opsgrids/client";
import type { RevealHeaders, UseStepUpResult } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  Avatar,
  BuildingUserIcon,
  Button,
  Card,
  ErrorState,
  InlineAlert,
  MapPinIcon,
  PageHeader,
  RevealableField,
  SectionHeader,
  Skeleton,
  StepUpDialog,
  UsersIcon,
  useToast,
} from "@opsgrids/ui";

import { getEmployee, listEmployeeReports, unmaskEmployeePii, USE_FIXTURES } from "@/api/client";
import { getChain } from "@/api/approvals";
import { getOrgUnitNameById } from "@/api/orgUnits";
import type {
  ApprovalChainOut,
  ChainRungOut,
  EmployeeOut,
  SensitiveField,
} from "@/api/types";
import { resolveDepartment } from "@/data/departments";
import { employmentLabel } from "@/lib/format";
import { EmployeeForm } from "@/ui/EmployeeForm";
import { EmployeeStatusPill } from "@/ui";
import { PersonRefBlock } from "@/ui/PersonRef";
import { TransitionDialog } from "@/ui/TransitionDialog";

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

  useEffect(() => {
    setExpanded(false);
  }, [employeeId]);

  const rungs = state.kind === "ready" ? state.chain.chain : [];
  const shouldCollapse = rungs.length > CHAIN_COLLAPSE_THRESHOLD && !expanded;
  const firstRevealedLevel = shouldCollapse ? undefined : rungs[CHAIN_VISIBLE_HEAD]?.level;

  useLayoutEffect(() => {
    if (expanded && firstRevealedLevel !== undefined) {
      chainContainerRef.current
        ?.querySelector<HTMLElement>(`[data-rung-level="${firstRevealedLevel}"]`)
        ?.focus();
    }
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

const SENSITIVE_FIELDS: { key: SensitiveField; label: string; noun: string; kind: "phone" | "email" | "text" }[] = [
  { key: "date_of_birth", label: "Date of birth", noun: "date of birth", kind: "text" },
  { key: "personal_email", label: "Personal email", noun: "personal email", kind: "email" },
  { key: "personal_phone", label: "Personal phone", noun: "personal phone", kind: "phone" },
  { key: "pan", label: "PAN", noun: "PAN", kind: "text" },
  { key: "aadhaar", label: "Aadhaar", noun: "Aadhaar", kind: "text" },
  { key: "uan", label: "UAN", noun: "UAN", kind: "text" },
  { key: "esi", label: "ESI", noun: "ESI", kind: "text" },
  { key: "bank_account", label: "Bank account", noun: "bank account", kind: "text" },
];

/** What fixture mode hands the unmask call in place of a step-up token: fixtures read no header. */
const NO_STEP_UP: RevealHeaders = { Authorization: "" };

/** One sealed field: the shared reveal hook and field, unmasking only this field on a code. */
function PiiField({
  employeeId,
  field,
  masked,
  canReveal,
  stepUp,
}: {
  employeeId: string;
  field: (typeof SENSITIVE_FIELDS)[number];
  masked: string;
  canReveal: boolean;
  stepUp: UseStepUpResult;
}) {
  const revealed = useRevealedField({
    resetKey: employeeId,
    // Fixture mode has no auth service to step up against (shortcut: no fixture step-up exists).
    stepUp: USE_FIXTURES ? { ...stepUp, run: (reveal) => reveal(NO_STEP_UP) } : stepUp,
    subject: "employee",
    noun: field.noun,
    load: async (headers) =>
      (await unmaskEmployeePii(employeeId, { fields: [field.key] }, revealRequest(headers)))[field.key] ?? null,
  });
  return (
    <div className="flex flex-col gap-1 rounded-tile bg-ground px-3 py-2">
      <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{field.label}</dt>
      <dd className="text-sm text-ink">
        <RevealableField
          kind={field.kind}
          label={field.label}
          noun={field.noun}
          masked={masked}
          canReveal={canReveal}
          field={revealed}
          stepUpIdle={stepUp.state === "idle"}
        />
      </dd>
    </div>
  );
}

/** The masked PII block. Each field reveals behind a one-time code (the unmask route needs
 *  `amr` `otp`, which a step-up token carries), shows in place for a short while and hides again;
 *  the value is never cached and never survives another record or leaving the page.
 *  `hrms.people.employee.read_sensitive` gates the buttons; the server decides. */
function PiiSection({ employee }: { employee: EmployeeOut }) {
  const canReadSensitive = useCan("hrms.people.employee.read_sensitive", "OWN");
  const stepUp = useStepUp();

  if (!employee.pii) return null;
  const pii = employee.pii;

  return (
    <Card className="p-6">
      <SectionHeader title="Sensitive details" subtitle="Masked by default. Revealing one needs a one-time code and is recorded." />
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {SENSITIVE_FIELDS.map((field) => {
          const masked = pii[field.key];
          if (masked === null) return null;
          return (
            <PiiField
              key={field.key}
              employeeId={employee.id}
              field={field}
              masked={masked}
              canReveal={canReadSensitive}
              stepUp={stepUp}
            />
          );
        })}
        {pii.bank_ifsc ? (
          <div className="flex flex-col gap-1 rounded-tile bg-ground px-3 py-2">
            <dt className="text-xs font-semibold uppercase tracking-wide text-faint">Bank IFSC</dt>
            <dd className="tnum text-sm text-ink">{pii.bank_ifsc}</dd>
          </div>
        ) : null}
      </dl>
      <StepUpDialog {...stepUp.dialogProps} />
    </Card>
  );
}

function ReportsSection({ employeeId }: { employeeId: string }) {
  const [reports, setReports] = useState<EmployeeOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReports(null);
    setError(null);
    listEmployeeReports(employeeId)
      .then(setReports)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Something went wrong"));
  }, [employeeId]);

  return (
    <Card className="p-6">
      <SectionHeader title="Direct reports" />
      <div className="mt-4">
        {error ? <ErrorState message={error} /> : null}
        {reports === null && !error ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-10 w-full rounded-tile" />)}
          </div>
        ) : null}
        {reports !== null && reports.length === 0 ? (
          <p className="text-sm text-muted">No one reports to this employee.</p>
        ) : null}
        {reports !== null && reports.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {reports.map((r) => (
              <li key={r.id}>
                <Link
                  to={`/employees/${r.id}`}
                  className="flex items-center gap-3 rounded-tile px-2 py-2 hover:bg-ground/60"
                >
                  <Avatar name={r.full_name} size="sm" />
                  <span className="flex flex-col leading-tight">
                    <span className="font-semibold text-ink text-sm">{r.full_name}</span>
                    <span className="text-xs text-muted tnum">{r.employee_no}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

function ManagerRef({ managerId }: { managerId: string }) {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    getEmployee(managerId)
      .then((m) => {
        if (active) setName(m.full_name);
      })
      .catch(() => {
        if (active) setName("Employee record unavailable");
      });
    return () => {
      active = false;
    };
  }, [managerId]);
  return (
    <Link to={`/employees/${managerId}`} className="text-ink hover:text-accent hover:underline">
      {name ?? "…"}
    </Link>
  );
}

const TRANSITIONABLE = new Set<EmployeeOut["status"]>(["probation", "active", "on_notice"]);

export function EmployeeDetailPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const toast = useToast();
  const [state, setState] = useState<EmployeeState>({ kind: "loading" });
  const [orgUnitMap, setOrgUnitMap] = useState<ReadonlyMap<string, string>>(new Map());
  const [editing, setEditing] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const canWrite = useCan("hrms.people.employee.write", "ORG");

  const load = useCallback(() => {
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

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getOrgUnitNameById().then(setOrgUnitMap);
  }, []);

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
    return <ErrorState message={state.message} onRetry={load} />;
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
        actions={
          <div className="flex items-center gap-2">
            <EmployeeStatusPill status={employee.status} />
            {canWrite ? (
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                Edit
              </Button>
            ) : null}
            {canWrite && TRANSITIONABLE.has(employee.status) ? (
              <Button variant="dark" size="sm" onClick={() => setTransitioning(true)}>
                Change status
              </Button>
            ) : null}
          </div>
        }
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
              {resolveDepartment(employee, orgUnitMap)}
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
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Date of joining
            </span>
            <span className="tnum text-ink">{employee.date_of_joining ?? "—"}</span>
          </div>
          {employee.status === "exited" ? (
            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                Date of exit
              </span>
              <span className="tnum text-ink">{employee.date_of_exit ?? "—"}</span>
            </div>
          ) : null}
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Reporting manager
            </span>
            <span className="text-ink">
              {employee.reporting_manager_id ? (
                <ManagerRef managerId={employee.reporting_manager_id} />
              ) : (
                "—"
              )}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Dotted-line manager
            </span>
            <span className="text-ink">
              {employee.dotted_line_manager_id ? (
                <ManagerRef managerId={employee.dotted_line_manager_id} />
              ) : (
                "—"
              )}
            </span>
          </div>
        </div>
      </Card>

      <PiiSection employee={employee} />
      <ReportsSection employeeId={employee.id} />
      <ApprovalChainSection employeeId={employee.id} firstName={firstName} />

      {editing ? (
        <EmployeeForm
          employee={employee}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            toast.success(`${saved.full_name} saved.`);
            setState({ kind: "ready", employee: saved });
          }}
        />
      ) : null}
      {transitioning ? (
        <TransitionDialog
          employee={employee}
          onClose={() => setTransitioning(false)}
          onSaved={(saved) => {
            toast.success(`${saved.full_name} is now ${saved.status.replace("_", " ")}.`);
            setState({ kind: "ready", employee: saved });
          }}
        />
      ) : null}
    </div>
  );
}
