import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { PermissionProvider } from "../permissions/PermissionProvider";
import { useGlobalSearch } from "./useGlobalSearch";

function page(items: readonly unknown[]) {
  return { items, total: items.length, page: 1, size: items.length || 1, pages: 1 };
}

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Routes a mocked `fetch` by substring so each test only has to describe the endpoints it cares
 *  about — anything unmatched answers an empty page, the same shape a real "no rows" list does. */
function fetchRouter(routes: Record<string, () => Response | Promise<Response>>) {
  return vi.fn((url: string) => {
    for (const [needle, handler] of Object.entries(routes)) {
      if (url.includes(needle)) return Promise.resolve(handler());
    }
    return Promise.resolve(respond(page([])));
  });
}

function Probe({ query, debounceMs = 5 }: { query: string; debounceMs?: number | undefined }) {
  const state = useGlobalSearch(query, { debounceMs });
  return (
    <div>
      <p>status: {state.status}</p>
      <p>partial: {String(state.partial)}</p>
      {state.error ? <p>error: {state.error.message}</p> : null}
      <ul>
        {state.groups.map((group) => (
          <li key={group.key}>
            <p>group: {group.label}</p>
            <ul>
              {group.items.map((item) => (
                <li key={item.key}>
                  {item.label} — {item.href}
                  {item.sublabel ? ` (${item.sublabel})` : ""}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Harness({
  query,
  grants,
  entitlements,
  debounceMs,
}: {
  query: string;
  grants: readonly string[];
  entitlements: readonly string[];
  debounceMs?: number;
}) {
  return (
    <PermissionProvider grants={grants} entitlements={entitlements}>
      <Probe query={query} debounceMs={debounceMs} />
    </PermissionProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useGlobalSearch", () => {
  it("is idle before anything is typed, and makes no request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<Harness query="" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} />);
    expect(screen.getByText("status: idle")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("searches only a product the caller is both entitled to and granted in, grouped by resource", async () => {
    const fetchMock = fetchRouter({
      "/employees": () =>
        respond(page([{ id: "e-1", full_name: "Ana Rao", designation: "Engineer", job_title: "" }])),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness query="ana" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} />);

    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(screen.getByText("group: Employees")).toBeTruthy();
    expect(screen.getByText("Ana Rao — https://people.opsgrids.com/employees/e-1 (Engineer)")).toBeTruthy();

    const urls = fetchMock.mock.calls.map((call) => call[0] as string);
    expect(urls.some((url) => url.includes("/employees?q=ana&size=5"))).toBe(true);
    expect(urls.some((url) => url.includes("/customers"))).toBe(false);
    expect(urls.some((url) => url.includes("/deals"))).toBe(false);
  });

  it("hides a product that is entitled but carries no grant, and one granted but not entitled", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(page([{ id: "c-1", name: "Acme", industry: "", city: "" }])));
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(<Harness query="acme" grants={[]} entitlements={["crm"]} />);
    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();

    rerender(<Harness query="acme" grants={["crm.book.company.read:ORG"]} entitlements={[]} />);
    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports success with no groups when nothing matched", async () => {
    const fetchMock = fetchRouter({});
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness query="zzz" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} />);

    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(screen.queryByText(/group:/)).toBeNull();
  });

  it("returns an empty success with no request when no product is searchable at all", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness query="anything" grants={[]} entitlements={[]} />);

    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports error only once every visible source has failed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond({ title: "Server error" }, 500));
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness query="ana" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} />);

    await waitFor(() => expect(screen.getByText("status: error")).toBeTruthy());
    expect(screen.getByText(/Search failed/)).toBeTruthy();
  });

  it("marks the search partial when one source fails but another still returns results", async () => {
    const fetchMock = fetchRouter({
      "/employees": () => respond({ title: "Server error" }, 500),
      "/customers": () =>
        respond(page([{ id: "c-1", name: "Acme Corp", industry: "Manufacturing", city: "" }])),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        query="a"
        grants={["hrms.people.employee.read:ORG", "crm.book.company.read:ORG"]}
        entitlements={["hrms", "crm"]}
      />,
    );

    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(screen.getByText("partial: true")).toBeTruthy();
    expect(screen.getByText("group: Companies")).toBeTruthy();
    expect(screen.queryByText("group: Employees")).toBeNull();
  });

  it("aborts a superseded request and shows only the latest query's results", async () => {
    let firstSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("/customers") && url.includes("q=ac")) {
        firstSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      }
      if (url.includes("/customers") && url.includes("q=be")) {
        return Promise.resolve(respond(page([{ id: "c-2", name: "Beta Corp", industry: "", city: "" }])));
      }
      return Promise.resolve(respond(page([])));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <Harness query="ac" grants={["crm.book.company.read:ORG"]} entitlements={["crm"]} />,
    );
    await waitFor(() => expect(firstSignal).toBeTruthy());

    rerender(<Harness query="be" grants={["crm.book.company.read:ORG"]} entitlements={["crm"]} />);

    await waitFor(() => expect(firstSignal?.aborted).toBe(true));
    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(screen.getByText(/Beta Corp/)).toBeTruthy();
    expect(screen.queryByText(/Acme/)).toBeNull();
  });

  it("debounces so a fast run of keystrokes fires one search, not one per keystroke", async () => {
    const fetchMock = fetchRouter({
      "/employees": () => respond(page([{ id: "e-1", full_name: "Ana Rao", designation: "", job_title: "" }])),
    });
    vi.stubGlobal("fetch", fetchMock);

    // Mounts idle (empty query) so every one of "a" / "an" / "ana" below is a debounced change,
    // the same as a person typing into an already-open search box — not the mount value itself,
    // which (like `useDebouncedValue`) is returned immediately rather than held back.
    const { rerender } = render(
      <Harness query="" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} debounceMs={30} />,
    );
    rerender(
      <Harness query="a" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} debounceMs={30} />,
    );
    rerender(
      <Harness query="an" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} debounceMs={30} />,
    );
    rerender(
      <Harness query="ana" grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]} debounceMs={30} />,
    );

    await waitFor(() => expect(screen.getByText("status: success")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((fetchMock.mock.calls[0]?.[0] as string).includes("q=ana")).toBe(true);
  });
});
