/**
 * Every call the dashboard makes, in one place, typed against `./types`. All go through
 * `@opsgrids/client`'s `apiRequest`: same-origin `/api/v1`, bearer attached from the session,
 * RFC 7807 problems raised as `ApiError`.
 */
import { apiRequest } from "@opsgrids/client";

import type {
  Activation,
  AttendanceRecord,
  Company,
  CreditBalance,
  DealActivity,
  Domain,
  DomainCreate,
  FinanceCustomerSummary,
  FinanceInvoiceSummary,
  FinanceOverview,
  Invitation,
  InvitationCreate,
  InvitationCreated,
  Invoice,
  LeaveBalance,
  Location,
  LocationCreate,
  LocationPatch,
  Member,
  MemberPatch,
  Organization,
  OrganizationPatch,
  OrgUnit,
  OrgUnitCreate,
  OrgUnitPatch,
  Page,
  Permission,
  Plan,
  Product,
  ProjectTask,
  Role,
  RoleWrite,
  SignupRequest,
  SignupResponse,
  Subscription,
} from "./types";

/** Build a query string from the keys that are actually set. `false`/`0` survive; `undefined` doesn't. */
function query(params: Record<string, string | number | boolean | undefined>): string {
  const parts = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number | boolean][];
  if (parts.length === 0) return "";
  const search = new URLSearchParams();
  for (const [key, value] of parts) search.set(key, String(value));
  return `?${search.toString()}`;
}

// ------------------------------------------------------------------ before there is a session

/** `POST /account/signup` — public, no bearer. */
export function signup(payload: SignupRequest): Promise<SignupResponse> {
  return apiRequest<SignupResponse>("/account/signup", {
    method: "POST",
    body: payload,
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/password/forgot` — public; always 202, whether or not the address exists. */
export function requestPasswordLink(email: string): Promise<void> {
  return apiRequest<void>("/auth/password/forgot", {
    method: "POST",
    body: { email },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/password/reset` — public; the token is the credential. */
export function setPasswordFromLink(token: string, newPassword: string): Promise<void> {
  return apiRequest<void>("/auth/password/reset", {
    method: "POST",
    body: { token, new_password: newPassword },
    auth: false,
    signalUnauthorized: false,
  });
}

// ------------------------------------------------------------------ the marketplace

/** `GET /products` — the catalogue this deployment sells. */
export function listProducts(): Promise<Product[]> {
  return apiRequest<Product[]>("/products");
}

/** `GET /products/activations` — what this organisation may use today. */
export function listActivations(): Promise<Activation[]> {
  return apiRequest<Activation[]>("/products/activations");
}

/** `GET /plans?product_key=…` — the published plans a customer may be sold for one product. */
export function listPlans(productKey: string): Promise<Plan[]> {
  return apiRequest<Plan[]>(`/plans?product_key=${encodeURIComponent(productKey)}`);
}

// ------------------------------------------------------------------ money

/** `GET /subscriptions` — this organisation's subscriptions. */
export function listSubscriptions(): Promise<Subscription[]> {
  return apiRequest<Subscription[]>("/subscriptions");
}

/**
 * `POST /subscriptions` — "Start a 14-day trial". This is what a marketplace button does
 * (ADR-0013 §1): the subscription is the thing bought, the activation follows it by event.
 */
export function startTrial(productKey: string, planVersionId: string, trialDays = 14): Promise<Subscription> {
  return apiRequest<Subscription>("/subscriptions", {
    method: "POST",
    body: { product_key: productKey, plan_version_id: planVersionId, trial_days: trialDays },
  });
}

/** `GET /invoices` — frozen invoices, newest first as the service returns them. */
export function listInvoices(): Promise<Invoice[]> {
  return apiRequest<Invoice[]>("/invoices");
}

/** `POST /subscriptions/{id}/cancel` — immediate. */
export function cancelSubscription(id: string, reason?: string): Promise<Subscription> {
  return apiRequest<Subscription>(`/subscriptions/${id}/cancel`, {
    method: "POST",
    body: reason ? { reason } : {},
  });
}

/** `POST /subscriptions/{id}/change-plan`. */
export function changeSubscriptionPlan(id: string, planVersionId: string): Promise<Subscription> {
  return apiRequest<Subscription>(`/subscriptions/${id}/change-plan`, {
    method: "POST",
    body: { plan_version_id: planVersionId },
  });
}

/** `GET /credit-balance?currency=…` — `currency` is required by the service. */
export function getCreditBalance(currency: string): Promise<CreditBalance> {
  return apiRequest<CreditBalance>(`/credit-balance${query({ currency })}`);
}

// ------------------------------------------------------------------ organization profile

/** `GET /organization`. */
export function getOrganization(): Promise<Organization> {
  return apiRequest<Organization>("/organization");
}

/** `PATCH /organization` — send only changed fields; the service drops `null`s, not clears them. */
export function updateOrganization(patch: OrganizationPatch): Promise<Organization> {
  return apiRequest<Organization>("/organization", { method: "PATCH", body: patch });
}

// ------------------------------------------------------------------ org units

/** `GET /org-units` — the whole tree in one call. */
export function listOrgUnits(): Promise<OrgUnit[]> {
  return apiRequest<OrgUnit[]>("/org-units");
}

/** `POST /org-units`. */
export function createOrgUnit(payload: OrgUnitCreate): Promise<OrgUnit> {
  return apiRequest<OrgUnit>("/org-units", { method: "POST", body: payload });
}

/** `PATCH /org-units/{id}` — rename, change kind/head, or deactivate. `code` is immutable. */
export function updateOrgUnit(id: string, patch: OrgUnitPatch): Promise<OrgUnit> {
  return apiRequest<OrgUnit>(`/org-units/${id}`, { method: "PATCH", body: patch });
}

/** `POST /org-units/{id}/move?parent_id=…` — query string, no body; omit to make it a root. */
export function moveOrgUnit(id: string, parentId: string | null): Promise<OrgUnit> {
  return apiRequest<OrgUnit>(`/org-units/${id}/move${query({ parent_id: parentId ?? undefined })}`, {
    method: "POST",
  });
}

// ------------------------------------------------------------------ locations

/** `GET /locations`. */
export function listLocations(): Promise<Location[]> {
  return apiRequest<Location[]>("/locations");
}

/** `POST /locations`. */
export function createLocation(payload: LocationCreate): Promise<Location> {
  return apiRequest<Location>("/locations", { method: "POST", body: payload });
}

/** `PATCH /locations/{id}` — `code` is immutable. */
export function updateLocation(id: string, patch: LocationPatch): Promise<Location> {
  return apiRequest<Location>(`/locations/${id}`, { method: "PATCH", body: patch });
}

// ------------------------------------------------------------------ members

export interface ListMembersParams {
  // `| undefined` explicitly, not just `?:` — the caller builds this from ternaries
  // (`query || undefined`), and `exactOptionalPropertyTypes` treats an *explicit* `undefined`
  // as a different shape from the key being absent unless the type says both are allowed.
  q?: string | undefined;
  org_unit_id?: string | undefined;
  status?: string | undefined;
  linked?: boolean | undefined;
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /members` — paged, filtered, sorted by `full_name` server-side (no sort param). */
export function listMembers(params: ListMembersParams): Promise<Page<Member>> {
  // A named interface has no index signature of its own, so it is not directly assignable to
  // `query`'s `Record<string, …>` parameter even though every field it declares fits — the cast
  // is the accepted way around that particular TypeScript rule, not a loosening of it.
  return apiRequest<Page<Member>>(`/members${query(params as Record<string, string | number | boolean | undefined>)}`);
}

/** `GET /members/{id}`. */
export function getMember(id: string): Promise<Member> {
  return apiRequest<Member>(`/members/${id}`);
}

/** `PATCH /members/{id}` — send only changed fields. */
export function updateMember(id: string, patch: MemberPatch): Promise<Member> {
  return apiRequest<Member>(`/members/${id}`, { method: "PATCH", body: patch });
}

/** `PUT /members/{id}/roles` — full replacement, not a diff. */
export function setMemberRoles(id: string, roleCodes: string[]): Promise<Member> {
  return apiRequest<Member>(`/members/${id}/roles`, { method: "PUT", body: { role_codes: roleCodes } });
}

/** `DELETE /members/{id}?reason=…` — soft: the membership is marked `removed`, the user survives. */
export function removeMember(id: string, reason?: string): Promise<void> {
  return apiRequest<void>(`/members/${id}${query({ reason })}`, { method: "DELETE" });
}

// ------------------------------------------------------------------ invitations

/** `GET /invitations` — pending only. */
export function listInvitations(): Promise<Invitation[]> {
  return apiRequest<Invitation[]>("/invitations");
}

/** `POST /invitations` — the 201 carries `token` exactly once; the service sends no email. */
export function createInvitation(payload: InvitationCreate): Promise<InvitationCreated> {
  return apiRequest<InvitationCreated>("/invitations", { method: "POST", body: payload });
}

/** `DELETE /invitations/{id}`. */
export function revokeInvitation(id: string): Promise<void> {
  return apiRequest<void>(`/invitations/${id}`, { method: "DELETE" });
}

/** `POST /invitations/accept` — public; one opaque 404 for every failure. */
export function acceptInvitation(token: string, fullName: string): Promise<Member> {
  return apiRequest<Member>("/invitations/accept", {
    method: "POST",
    body: { token, full_name: fullName },
    auth: false,
    signalUnauthorized: false,
  });
}

// ------------------------------------------------------------------ roles and permissions

/** `GET /roles`. */
export function listRoles(): Promise<Role[]> {
  return apiRequest<Role[]>("/roles");
}

/** `POST /roles`. */
export function createRole(payload: RoleWrite): Promise<Role> {
  return apiRequest<Role>("/roles", { method: "POST", body: payload });
}

/** `PUT /roles/{id}` — must carry `code` and `name` unchanged alongside `grants`. */
export function updateRole(id: string, payload: RoleWrite): Promise<Role> {
  return apiRequest<Role>(`/roles/${id}`, { method: "PUT", body: payload });
}

/** `DELETE /roles/{id}` — refused for system roles (422) and roles still in use (409). */
export function deleteRole(id: string): Promise<void> {
  return apiRequest<void>(`/roles/${id}`, { method: "DELETE" });
}

/** `GET /permissions` — the read-only catalogue the grant picker is built from. */
export function listPermissions(): Promise<Permission[]> {
  return apiRequest<Permission[]>("/permissions");
}

// ------------------------------------------------------------------ email domains (SSO)

/** `GET /domains`. */
export function listDomains(): Promise<Domain[]> {
  return apiRequest<Domain[]>("/domains");
}

/** `POST /domains` — claims a fresh, unverified domain, or re-claims (rotating the token
 * shown) one this organisation already holds and has not yet verified. */
export function claimDomain(payload: DomainCreate): Promise<Domain> {
  return apiRequest<Domain>("/domains", { method: "POST", body: payload });
}

/** `POST /domains/{id}/verify` — checks DNS now. Rate limited per organisation. */
export function verifyDomain(id: string): Promise<Domain> {
  return apiRequest<Domain>(`/domains/${id}/verify`, { method: "POST" });
}

/** `DELETE /domains/{id}`. */
export function releaseDomain(id: string): Promise<void> {
  return apiRequest<void>(`/domains/${id}`, { method: "DELETE" });
}

// ------------------------------------------------------------------ the owner/executive home
//
// Every tenant token shares audience `app` (`services/app-gateway/app/config.py`), so this same
// session reads every product's own summary endpoints through the gateway. `src/routes/Home.tsx`
// calls each of these only once the caller's entitlement and grants say it may — never to find
// out whether it may.

/** `GET /finance/overview`, trimmed to `revenue_history` (see `FinanceOverview`). Needs
 * `finance.reports.summary.read:ORG`. */
export function getFinanceOverview(): Promise<FinanceOverview> {
  return apiRequest<FinanceOverview>("/finance/overview");
}

/** `GET /finance/invoices` — newest-first by the service's own default (`sort=issued_desc`), so
 * `size` alone gives the most recent documents with no extra sort parameter. A `status` narrows
 * to one lifecycle state, for a `Page.total` count. Needs `finance.billing.invoice.read:ORG`. */
export function listFinanceInvoices(params: { status?: string; size: number }): Promise<Page<FinanceInvoiceSummary>> {
  return apiRequest<Page<FinanceInvoiceSummary>>(
    `/finance/invoices${query({ status: params.status, size: params.size })}`,
  );
}

/** `GET /finance/customers` — a bounded directory read to resolve `customer_id` to a name, the
 * same convenience `product-finance`'s own `getCustomerById` uses; never the page a person
 * browses. Needs `finance.billing.customer.read:ORG`. */
export function listFinanceCustomers(): Promise<Page<FinanceCustomerSummary>> {
  return apiRequest<Page<FinanceCustomerSummary>>(`/finance/customers${query({ size: 200 })}`);
}

/** `GET /customers?status=customer&size=1` → `Page.total` — companies marked as customers.
 * Needs `crm.book.company.read`. */
export function countCrmCustomers(): Promise<Page<Company>> {
  return apiRequest<Page<Company>>(`/customers${query({ status: "customer", size: 1 })}`);
}

/** `GET /deals/activities` — real timestamps (`occurred_at`), newest first. Needs
 * `crm.pipeline.activity.read`. */
export function listDealActivities(size: number): Promise<Page<DealActivity>> {
  return apiRequest<Page<DealActivity>>(`/deals/activities${query({ size })}`);
}

/** `GET /projects-workspace/tasks?assignee=me` — resolved server-side to the caller's own cards,
 * so one bounded call gives both the Pending Tasks count and the Tasks list, never a scan. Needs
 * `projects.board.task.read`. */
export function listMyTasks(size: number): Promise<Page<ProjectTask>> {
  return apiRequest<Page<ProjectTask>>(`/projects-workspace/tasks${query({ assignee: "me", size })}`);
}

// ------------------------------------------------------------------ the employee workspace
//
// `src/ui/home/EmployeeWorkspace.tsx`'s own self-service reads — HRMS routes `/attendance` and
// `/leave` straight to the tenant's HRMS services (`services/app-gateway/routes.json`), scoped
// server-side to the caller's own record by `employee_id`, never by anything this app decides.

/** `GET /attendance?employee_id=…&date_from=…&date_to=…` — one caller's own record for one work
 * day, `date` as `yyyy-mm-dd`. Empty when they have not checked in yet: a punch is what creates
 * the row. Needs `hrms.time.attendance.read`. */
export function getMyAttendance(employeeId: string, date: string): Promise<Page<AttendanceRecord>> {
  return apiRequest<Page<AttendanceRecord>>(
    `/attendance${query({ employee_id: employeeId, date_from: date, date_to: date, size: 1 })}`,
  );
}

/** `GET /leave/balances?employee_id=…` — every leave type this employee has a balance for, in
 * half-days. Needs `hrms.time.leave_balance.read`. */
export function listMyLeaveBalances(employeeId: string): Promise<LeaveBalance[]> {
  return apiRequest<LeaveBalance[]>(`/leave/balances${query({ employee_id: employeeId })}`);
}
