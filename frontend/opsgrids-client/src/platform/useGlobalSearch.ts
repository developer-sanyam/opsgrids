/**
 * Global search across the products the caller can actually open — one text query fanned out to
 * the list endpoints that already accept `q`, five rows each, grouped by product and resource.
 *
 * There is no search service and this does not invent one: it calls the same list endpoints
 * `product-*`'s own screens already page through (`GET /customers`, `/deals`, `/employees`, …),
 * the same same-origin `/api/v1` prefix every app proxies to its gateway, with the bearer token
 * from the session — tenant scoping comes from that token exactly as it does for any other list
 * call, not from anything this hook adds.
 *
 * A source only runs when `usePlatformModules()` says the product is both entitled and granted —
 * the same "activated and the caller holds a grant in it" reading `AppShell v2`'s own nav uses, so
 * a result never appears for a product the caller cannot otherwise open. This hides UI, it is not
 * authorization: every one of these endpoints still scopes and refuses on its own.
 *
 * The query is debounced internally, and every request racing a superseded query is aborted (not
 * merely ignored) — `apiRequest`'s `signal` option, not a bespoke `fetch`. A source failing (a
 * timeout, a 500) does not blank the others: `partial` says some results may be incomplete rather
 * than turning a half-successful search into an error screen.
 */

import { useEffect, useMemo, useState } from "react";

import { apiRequest } from "../http";
import { getPlatformHost } from "./catalogue";
import type { PlatformProductKey } from "./catalogue";
import { usePlatformModules } from "./usePlatformModules";

/** How many rows each source contributes — a palette, not a report. */
const RESULTS_PER_SOURCE = 5;

/** Matches `useDebouncedValue`'s own default in `@opsgrids/ui`'s `SearchInput`. */
const DEFAULT_DEBOUNCE_MS = 300;

interface WirePage<T> {
  items: readonly T[];
}

export interface GlobalSearchResultItem {
  /** Stable across renders — `${group}-${row id}` — safe as a React list key. */
  key: string;
  label: string;
  sublabel: string | null;
  /** An absolute URL on the product's own host (`getPlatformHost`) — this hook never navigates,
   *  it only ever hands back where to. */
  href: string;
}

export interface GlobalSearchGroup {
  /** Stable across renders — `${product}:${group label}`. */
  key: string;
  product: PlatformProductKey;
  /** The section label a command palette groups these rows under, e.g. "Companies". */
  label: string;
  items: readonly GlobalSearchResultItem[];
}

export type GlobalSearchStatus = "idle" | "loading" | "success" | "error";

export interface GlobalSearchState {
  /**
   * `idle` before anything has been typed (no request has been made — there is nothing to be
   * loading, empty or wrong about yet). `loading` while the debounced query's requests are in
   * flight. `success` once they have settled, `groups` empty when nothing matched or the caller
   * has no searchable product open — an empty result, not a failure. `error` only when every
   * source that ran failed.
   */
  status: GlobalSearchStatus;
  groups: readonly GlobalSearchGroup[];
  /** `true` when at least one source failed while at least one other succeeded: `groups` is real
   *  but incomplete, worth a quiet "some results may be missing" rather than nothing at all. */
  partial: boolean;
  error: Error | null;
  /** `"identifier"` when the query looks like an email or a phone number, so nothing was sent. */
  blocked?: "identifier" | undefined;
}

const IDLE_STATE: GlobalSearchState = { status: "idle", groups: [], partial: false, error: null };

/**
 * An email or a phone number is never put in a `?q=` URL: gateway and proxy logs keep the URL.
 * Those are looked up through POST bodies on the record's own screen (spec §4.4.6).
 */
function looksLikeIdentifier(query: string): boolean {
  return query.includes("@") || /^[\d\s+().-]{6,}$/.test(query);
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

type GroupFetcher = (query: string, signal: AbortSignal) => Promise<GlobalSearchGroup>;

interface RegisteredSource {
  product: PlatformProductKey;
  fetchGroup: GroupFetcher;
}

/** One entry per resource: the list path (mirroring the query params each service's OpenAPI
 *  contract already documents), and how a row becomes a result. Nothing here duplicates a
 *  product's own wire types — these are the handful of read-only fields a search result needs,
 *  not the object each service returns. */
function registerSource<TRow>(
  product: PlatformProductKey,
  group: string,
  path: (query: string, limit: number) => string,
  toItem: (row: TRow, host: string) => GlobalSearchResultItem,
): RegisteredSource {
  return {
    product,
    fetchGroup: async (query, signal) => {
      const host = getPlatformHost(product);
      const page = await apiRequest<WirePage<TRow>>(path(query, RESULTS_PER_SOURCE), { signal });
      return {
        key: `${product}:${group}`,
        product,
        label: group,
        items: page.items.map((row) => toItem(row, host)),
      };
    },
  };
}

function joinSublabel(...parts: (string | null | undefined)[]): string | null {
  const joined = parts.filter((part): part is string => Boolean(part && part.trim())).join(" · ");
  return joined === "" ? null : joined;
}

interface CompanyRow {
  id: string;
  name: string;
  industry: string;
  city: string;
}

interface ContactRow {
  id: string;
  full_name: string;
  title: string;
  email: string;
}

interface DealRow {
  id: string;
  name: string;
  stage: string;
}

interface LeadRow {
  id: string;
  full_name: string;
  status: string;
  source: string;
}

interface EmployeeRow {
  id: string;
  full_name: string;
  designation: string;
  job_title: string;
}

interface TicketRow {
  id: string;
  subject: string;
  status: string;
}

interface InvoiceRow {
  id: string;
  number: string;
  status: string;
}

interface FinanceCustomerRow {
  customer: { id: string; name: string; city: string };
  standing: string;
}

interface InventoryItemRow {
  id: string;
  sku: string;
  name: string;
  warehouse: string;
}

interface SupplierRow {
  id: string;
  code: string;
  name: string;
  category: string;
}

interface ProjectRow {
  id: string;
  code: string;
  name: string;
  client: string;
}

const SEARCH_SOURCES: readonly RegisteredSource[] = [
  registerSource<CompanyRow>(
    "crm",
    "Companies",
    (query, limit) => `/customers?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `crm-company-${row.id}`,
      label: row.name,
      sublabel: joinSublabel(row.industry, row.city),
      href: `${host}/companies/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<ContactRow>(
    "crm",
    "Contacts",
    (query, limit) => `/contacts?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `crm-contact-${row.id}`,
      label: row.full_name,
      sublabel: joinSublabel(row.title, row.email),
      href: `${host}/contacts?q=${encodeURIComponent(row.full_name)}`,
    }),
  ),
  registerSource<DealRow>(
    "crm",
    "Deals",
    (query, limit) => `/deals?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `crm-deal-${row.id}`,
      label: row.name,
      sublabel: joinSublabel(row.stage),
      href: `${host}/deals/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<LeadRow>(
    "crm",
    "Leads",
    (query, limit) => `/leads?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `crm-lead-${row.id}`,
      label: row.full_name,
      sublabel: joinSublabel(row.status, row.source),
      href: `${host}/leads/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<EmployeeRow>(
    "hrms",
    "Employees",
    (query, limit) => `/employees?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `hrms-employee-${row.id}`,
      label: row.full_name,
      sublabel: joinSublabel(row.designation, row.job_title),
      href: `${host}/employees/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<ProjectRow>(
    "projects",
    "Projects",
    (query, limit) => `/projects-workspace/projects?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `projects-project-${row.id}`,
      label: row.name,
      sublabel: joinSublabel(row.code, row.client),
      href: `${host}/portfolio?q=${encodeURIComponent(row.name)}`,
    }),
  ),
  registerSource<TicketRow>(
    "support",
    "Support tickets",
    (query, limit) => `/support/tickets?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `support-ticket-${row.id}`,
      label: row.subject,
      sublabel: joinSublabel(row.status),
      href: `${host}/queue/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<InvoiceRow>(
    "finance",
    "Invoices",
    (query, limit) => `/finance/invoices?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `finance-invoice-${row.id}`,
      label: row.number,
      sublabel: joinSublabel(row.status),
      href: `${host}/invoices/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<FinanceCustomerRow>(
    "finance",
    "Finance customers",
    (query, limit) => `/finance/customers?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `finance-customer-${row.customer.id}`,
      label: row.customer.name,
      sublabel: joinSublabel(row.customer.city, row.standing),
      href: `${host}/customers?q=${encodeURIComponent(row.customer.name)}`,
    }),
  ),
  registerSource<InventoryItemRow>(
    "operations",
    "Inventory",
    (query, limit) => `/operations/inventory?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `operations-item-${row.id}`,
      label: row.name,
      sublabel: joinSublabel(row.sku, row.warehouse),
      href: `${host}/inventory/${encodeURIComponent(row.id)}`,
    }),
  ),
  registerSource<SupplierRow>(
    "operations",
    "Suppliers",
    (query, limit) => `/operations/suppliers?q=${encodeURIComponent(query)}&size=${limit}`,
    (row, host) => ({
      key: `operations-supplier-${row.id}`,
      label: row.name,
      sublabel: joinSublabel(row.code, row.category),
      href: `${host}/suppliers/${encodeURIComponent(row.id)}`,
    }),
  ),
];

export interface UseGlobalSearchOptions {
  /** Default 300ms — matches `useDebouncedValue`'s own default. */
  debounceMs?: number;
}

/**
 * Debounced, cancellable, permission-scoped search across every product the caller can open.
 *
 * `query` is read on every render — pass the raw input value, not something already debounced;
 * this hook owns its own debounce so two callers of the same input never double it up.
 */
export function useGlobalSearch(query: string, options: UseGlobalSearchOptions = {}): GlobalSearchState {
  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  const modules = usePlatformModules();
  // A primitive, content-based key rather than the `modules` array itself: `usePlatformModules`
  // (and the permission/entitlement arrays it reads) hand back a new array reference on every
  // render even when the *set* of visible products hasn't changed, and this drives a `useEffect`
  // dependency — an object identity that churns for no reason would refire the search, and every
  // request in flight, on every unrelated re-render.
  const visibleProductsKey = useMemo(
    () =>
      modules
        .map((module) => module.key)
        .sort()
        .join(","),
    [modules],
  );

  const trimmed = query.trim();
  const [debounced, setDebounced] = useState(trimmed);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(trimmed), debounceMs);
    return () => clearTimeout(timer);
  }, [trimmed, debounceMs]);

  const [state, setState] = useState<GlobalSearchState>(IDLE_STATE);

  useEffect(() => {
    if (debounced === "") {
      setState(IDLE_STATE);
      return;
    }
    if (looksLikeIdentifier(debounced)) {
      setState({ ...IDLE_STATE, blocked: "identifier" });
      return;
    }

    const visibleProducts = new Set(visibleProductsKey === "" ? [] : visibleProductsKey.split(","));
    const sources = SEARCH_SOURCES.filter((source) => visibleProducts.has(source.product));
    if (sources.length === 0) {
      // Signed in, but no product open holds a searchable resource — an empty result, not an
      // error: there is nothing wrong, there is just nothing this caller may search.
      setState({ status: "success", groups: [], partial: false, error: null });
      return;
    }

    const controller = new AbortController();
    let active = true;
    setState((previous) => ({ ...previous, status: "loading" }));

    void Promise.allSettled(sources.map((source) => source.fetchGroup(debounced, controller.signal))).then(
      (results) => {
        if (!active) return;
        const groups: GlobalSearchGroup[] = [];
        let failures = 0;
        let aborted = 0;
        for (const result of results) {
          if (result.status === "fulfilled") {
            if (result.value.items.length > 0) groups.push(result.value);
          } else if (isAbortError(result.reason)) {
            aborted += 1;
          } else {
            failures += 1;
          }
        }
        // Every settled promise was either a real failure or this run's own abort — a run that
        // aborts everything (superseded before anything came back) leaves the prior state alone
        // rather than flashing an error the caller never asked to see.
        if (aborted === results.length) return;
        if (failures > 0 && failures + aborted === results.length) {
          setState({
            status: "error",
            groups: [],
            partial: false,
            error: new Error("Search failed. Check your connection and try again."),
          });
        } else {
          setState({ status: "success", groups, partial: failures > 0, error: null });
        }
      },
    );

    return () => {
      active = false;
      controller.abort();
    };
  }, [debounced, visibleProductsKey]);

  return state;
}
