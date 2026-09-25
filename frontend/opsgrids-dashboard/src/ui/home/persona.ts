/**
 * Which Home the signed-in caller sees: the Owner/Executive page (`routes/Home.tsx`'s own body)
 * or "My Workspace" (`EmployeeWorkspace.tsx`), for a caller who holds none of that page's
 * owner-level grants (mock-5's "Employee View").
 *
 * Built on the same `resolveAccess` every Home widget already resolves through — entitlement
 * first, then the grant a read actually needs (`src/ui/home/access.ts`) — rather than a role
 * string off the token: a role name is a hint for copy, never an authorization boundary, and two
 * different organisations may staff the same product differently. `core.product.activation.write`
 * carries no entitlement of its own (it is a platform permission), so it is folded in beside the
 * `resolveAccess` results rather than run through it.
 *
 * Ambiguous — nobody has fired an owner-level *or* an employee-level widget's grant — resolves to
 * `"owner"`, the platform's long-standing default: that page already draws every one of its
 * widgets as a "No access" or "isn't active" notice rather than an empty screen, so it is the
 * honest fallback for a caller this function cannot place, and it is what every caller saw before
 * this file existed.
 */
import { useCan, useEntitlement, usePermissions } from "@opsgrids/client";

import { resolveAccess } from "./access";
import type { AccessState } from "./access";

export type HomePersona = "owner" | "employee";

export interface HomePersonaSignals {
  /** `usePermissions().status === "ready"` — while the grants fetch is still in flight (or
   * failed) this resolves to `"owner"`, the same page a caller already sees mid-load today, so a
   * caller is never bounced onto a different page a moment after the real answer lands. */
  ready: boolean;
  /** The Home KPI row's own ORG-scoped reads: real revenue, real invoices, a real customer book.
   * Any one of these being `"ready"` is what makes that page's business widgets actually draw. */
  ownerAccess: readonly AccessState[];
  /** `core.product.activation.write:ORG` — the one grant only an owner holds, gated on no
   * product's entitlement. */
  canActivateProducts: boolean;
  /** The Employee workspace's own reads: HRMS self-service, and the caller's own task list. */
  employeeAccess: readonly AccessState[];
  /** `core.approval.request.read:OWN` — a platform route every `MEMBER` holds (dashboard audit
   * §5.9), so it carries no product entitlement to run through `resolveAccess`. */
  canReadOwnApprovals: boolean;
}

export function resolveHomePersona(signals: HomePersonaSignals): HomePersona {
  if (!signals.ready) return "owner";
  const isOwner = signals.ownerAccess.some((state) => state === "ready") || signals.canActivateProducts;
  if (isOwner) return "owner";
  const isEmployee = signals.employeeAccess.some((state) => state === "ready") || signals.canReadOwnApprovals;
  return isEmployee ? "employee" : "owner";
}

/** The hook `routes/Home.tsx` switches on. Reads the same grants every widget on both pages
 * already reads; asks nothing of its own. */
export function useHomePersona(): HomePersona {
  const { status } = usePermissions();

  const financeEntitled = useEntitlement("finance");
  const crmEntitled = useEntitlement("crm");
  const hrmsEntitled = useEntitlement("hrms");
  const projectsEntitled = useEntitlement("projects");

  const canReadFinanceSummaryOrg = useCan("finance.reports.summary.read", "ORG");
  const canReadFinanceInvoicesOrg = useCan("finance.billing.invoice.read", "ORG");
  const canReadCrmCompaniesOrg = useCan("crm.book.company.read", "ORG");
  const canActivateProducts = useCan("core.product.activation.write", "ORG");

  const canWriteAttendanceOwn = useCan("hrms.time.attendance.write");
  const canWriteLeaveRequestOwn = useCan("hrms.time.leave_request.write");
  const canReadTasksOwn = useCan("projects.board.task.read");
  const canReadOwnApprovals = useCan("core.approval.request.read");

  return resolveHomePersona({
    ready: status === "ready",
    ownerAccess: [
      resolveAccess(financeEntitled, canReadFinanceSummaryOrg),
      resolveAccess(financeEntitled, canReadFinanceInvoicesOrg),
      resolveAccess(crmEntitled, canReadCrmCompaniesOrg),
    ],
    canActivateProducts,
    employeeAccess: [
      resolveAccess(hrmsEntitled, canWriteAttendanceOwn),
      resolveAccess(hrmsEntitled, canWriteLeaveRequestOwn),
      resolveAccess(projectsEntitled, canReadTasksOwn),
    ],
    canReadOwnApprovals,
  });
}
