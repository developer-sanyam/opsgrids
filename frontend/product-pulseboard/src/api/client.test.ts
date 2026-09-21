import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getPeople,
  getProjects,
  getStandups,
  getTasks,
  getTimesheets,
  listProjects,
} from "./client";

/** Stub `fetch` once, capture the request URL, and answer with the given JSON body. */
function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestedUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  const call = fetchMock.mock.calls[0] as [string, unknown] | undefined;
  if (!call) throw new Error("fetch was never called");
  return call[0];
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("listProjects", () => {
  it("requests the real project-service path with q, status and a full page size", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await listProjects({ search: "helios", status: "at_risk" });
    expect(requestedUrl(fetchMock)).toBe("/api/v1/projects?q=helios&status=at_risk&size=200");
  });

  it("omits q and status when unset, but still caps the page size", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await listProjects();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/projects?size=200");
  });

  it("never calls the product-segmented path the SPA used to call", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await listProjects();
    expect(requestedUrl(fetchMock)).not.toContain("/pulseboard/");
  });

  it("maps a wire project — including a null owner, start date and due date — honestly", async () => {
    stubFetch({
      items: [
        {
          id: "proj-1",
          code: "PB-01",
          name: "Helios Web Platform",
          client: "Helios Retail",
          status: "on_track",
          progress_pct: 62,
          budget_minor: 24_000_000,
          spent_minor: 15_000_000,
          currency: "USD",
          owner_id: null,
          start_date: null,
          due_date: null,
          billable: true,
          open_tasks: 14,
        },
      ],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    const [project] = await listProjects();
    expect(project).toEqual({
      id: "proj-1",
      code: "PB-01",
      name: "Helios Web Platform",
      client: "Helios Retail",
      status: "on_track",
      progress_pct: 62,
      budget_minor: 24_000_000,
      spent_minor: 15_000_000,
      currency: "USD",
      owner_id: null,
      start_date: null,
      due_date: null,
      billable: true,
      open_tasks: 14,
    });
  });
});

describe("getProjects", () => {
  it("requests the unfiltered engagement portfolio at a full page size", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await getProjects();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/projects?size=200");
  });
});

describe("getTasks", () => {
  it("requests the board's task list under /projects/tasks, not /pulseboard/tasks", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await getTasks();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/projects/tasks?size=200");
  });

  it("maps a wire task, keeping a null assignee and due date as null", async () => {
    stubFetch({
      items: [
        {
          id: "task-1",
          key: "PB-231",
          title: "Wire up saved-filter presets",
          status: "backlog",
          assignee_id: null,
          project_id: "proj-1",
          estimate_hours: 8,
          priority: "medium",
          tags: ["frontend"],
          due_date: null,
        },
      ],
      total: 1,
    });
    const [task] = await getTasks();
    expect(task).toEqual({
      id: "task-1",
      key: "PB-231",
      title: "Wire up saved-filter presets",
      status: "backlog",
      assignee_id: null,
      project_id: "proj-1",
      estimate_hours: 8,
      priority: "medium",
      tags: ["frontend"],
      due_date: null,
    });
  });
});

describe("getPeople", () => {
  it("requests the delivery-team roster under /projects/people", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await getPeople();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/projects/people?size=200");
  });

  it("maps a wire person one-to-one", async () => {
    stubFetch({
      items: [{ id: "p01", name: "Nadia Rahman", role: "Head of Delivery", location: "Bengaluru", capacity_hours: 20 }],
      total: 1,
    });
    const [person] = await getPeople();
    expect(person).toEqual({
      id: "p01",
      name: "Nadia Rahman",
      role: "Head of Delivery",
      location: "Bengaluru",
      capacity_hours: 20,
    });
  });
});

describe("getTimesheets", () => {
  // `TODAY`/`WEEK_START` are derived at module load from the real clock on the live path, so
  // proving that requires pinning the clock to a known instant and checking the literal week —
  // asserting against the module's own `WEEK_START` would just compare a value with itself and
  // could never catch a regression back to the pinned fixture week.
  it("requests the Monday of the real current week, not the pinned fixture week", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-21T10:00:00Z")); // a Monday
    vi.resetModules();
    const live = await import("./client");
    const fetchMock = stubFetch({ items: [], total: 0 });
    await live.getTimesheets();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/timesheets?week=2026-09-21&size=200");
  });

  it("maps a wire row and its cells", async () => {
    stubFetch({
      items: [
        {
          person_id: "p01",
          week_start: "2026-08-24",
          status: "submitted",
          cells: [{ date: "2026-08-24", entered_hours: 8, billable_hours: 6.5 }],
        },
      ],
      total: 1,
    });
    const [row] = await getTimesheets();
    expect(row).toEqual({
      person_id: "p01",
      week_start: "2026-08-24",
      status: "submitted",
      cells: [{ date: "2026-08-24", entered_hours: 8, billable_hours: 6.5 }],
    });
  });
});

describe("getStandups", () => {
  it("requests the day's check-ins from the standup service", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await getStandups("2026-08-24");
    expect(requestedUrl(fetchMock)).toBe("/api/v1/standups?date=2026-08-24&size=200");
  });

  it("defaults to the real current date when called with no argument, not the pinned fixture date", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-03T09:00:00Z"));
    vi.resetModules();
    const live = await import("./client");
    const fetchMock = stubFetch({ items: [], total: 0 });
    await live.getStandups();
    expect(requestedUrl(fetchMock)).toBe("/api/v1/standups?date=2026-11-03&size=200");
  });

  it("maps a wire entry, preserving a null blockers field", async () => {
    stubFetch({
      items: [
        {
          id: "s01",
          person_id: "p02",
          date: "2026-08-24",
          yesterday: "Shipped the thing",
          today: "Ship the next thing",
          blockers: null,
        },
      ],
      total: 1,
    });
    const [entry] = await getStandups("2026-08-24");
    expect(entry).toEqual({
      id: "s01",
      person_id: "p02",
      date: "2026-08-24",
      yesterday: "Shipped the thing",
      today: "Ship the next thing",
      blockers: null,
    });
  });
});
