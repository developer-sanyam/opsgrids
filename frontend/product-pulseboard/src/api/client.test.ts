import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createPerson,
  createProject,
  createTask,
  deletePerson,
  deleteProject,
  deleteTask,
  getPeople,
  getTimesheetSummary,
  listPeople,
  listProjects,
  listStandups,
  listTasks,
  listTimesheets,
  postStandup,
  recordTimesheetWeek,
  setTimesheetStatus,
  updatePerson,
  updateProject,
  updateTask,
} from "./client";

const ENVELOPE = { items: [], total: 0, page: 1, size: 25, pages: 0 };

/** Stub `fetch` once, capture the request, and answer with the given JSON body. A `null` body
 * answers with no body at all — required for a 204, which the Fetch spec refuses to carry one. */
function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(body === null ? null : JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requested(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  const [url, init] = call;
  return { url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("listProjects", () => {
  it("pages through the real envelope with q, status and sort", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listProjects({ q: "helios", status: "at_risk", sort: "due", page: 2, size: 10 });
    const { url, method } = requested(fetchMock);
    expect(method).toBe("GET");
    expect(url).toBe("/api/v1/projects?q=helios&status=at_risk&sort=due&page=2&size=10");
  });

  it("defaults to page 1 at the standard page size and omits unset filters", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listProjects();
    expect(requested(fetchMock).url).toBe("/api/v1/projects?page=1&size=25");
  });
});

describe("createProject", () => {
  it("POSTs the exact body to /projects", async () => {
    const fetchMock = stubFetch({
      id: "proj-1",
      code: "PB-20",
      name: "New Engagement",
      client: "",
      status: "on_track",
      progress_pct: 0,
      budget_minor: 0,
      spent_minor: 0,
      currency: "USD",
      owner_id: null,
      start_date: null,
      due_date: null,
      billable: true,
      open_tasks: 0,
    });
    const input = {
      code: "PB-20",
      name: "New Engagement",
      client: "",
      status: "on_track" as const,
      progress_pct: 0,
      budget_minor: 0,
      spent_minor: 0,
      currency: "USD",
      owner_ref: null,
      start_date: null,
      due_date: null,
      billable: true,
      open_tasks: 0,
    };
    await createProject(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects");
    expect(body).toEqual(input);
  });
});

describe("updateProject", () => {
  it("PATCHes only the changed fields to /projects/{code}", async () => {
    const fetchMock = stubFetch({
      id: "proj-1",
      code: "PB-01",
      name: "Helios",
      client: "",
      status: "blocked",
      progress_pct: 40,
      budget_minor: 0,
      spent_minor: 0,
      currency: "USD",
      owner_id: null,
      start_date: null,
      due_date: null,
      billable: true,
      open_tasks: 0,
    });
    await updateProject("PB-01", { status: "blocked", progress_pct: 40 });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects/PB-01");
    expect(body).toEqual({ status: "blocked", progress_pct: 40 });
  });
});

describe("deleteProject", () => {
  it("DELETEs /projects/{code}", async () => {
    const fetchMock = stubFetch(null, 204);
    await deleteProject("PB-01");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects/PB-01");
  });
});

describe("listTasks", () => {
  it("requests the board's task list with its filters", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listTasks({ status: "review", project: "PB-01", assignee: "p02" });
    expect(requested(fetchMock).url).toBe(
      "/api/v1/projects/tasks?status=review&project=PB-01&assignee=p02&page=1&size=200",
    );
  });
});

describe("createTask", () => {
  it("POSTs the exact body to /projects/tasks", async () => {
    const fetchMock = stubFetch({
      id: "task-1",
      key: "PB-300",
      title: "New card",
      status: "backlog",
      assignee_id: null,
      project_id: "proj-1",
      estimate_hours: 0,
      priority: "medium",
      tags: [],
      due_date: null,
    });
    const input = {
      key: "PB-300",
      project_code: "PB-01",
      title: "New card",
      status: "backlog" as const,
      assignee_ref: null,
      estimate_hours: 0,
      priority: "medium" as const,
      tags: [],
      due_date: null,
    };
    await createTask(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects/tasks");
    expect(body).toEqual(input);
  });
});

describe("updateTask", () => {
  it("PATCHes a status-only move to /projects/tasks/{key}", async () => {
    const fetchMock = stubFetch({
      id: "task-1",
      key: "PB-231",
      title: "x",
      status: "review",
      assignee_id: null,
      project_id: "proj-1",
      estimate_hours: 0,
      priority: "medium",
      tags: [],
      due_date: null,
    });
    await updateTask("PB-231", { status: "review" });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects/tasks/PB-231");
    expect(body).toEqual({ status: "review" });
  });

  it("sends an explicit null to clear the assignee, not an omitted field", async () => {
    const fetchMock = stubFetch({
      id: "task-1",
      key: "PB-231",
      title: "x",
      status: "backlog",
      assignee_id: null,
      project_id: "proj-1",
      estimate_hours: 0,
      priority: "medium",
      tags: [],
      due_date: null,
    });
    await updateTask("PB-231", { assignee_ref: null });
    expect(requested(fetchMock).body).toEqual({ assignee_ref: null });
  });
});

describe("deleteTask", () => {
  it("DELETEs /projects/tasks/{key}", async () => {
    const fetchMock = stubFetch(null, 204);
    await deleteTask("PB-231");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects/tasks/PB-231");
  });
});

describe("listPeople / getPeople", () => {
  it("pages the roster under /projects/people", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listPeople({ page: 2, size: 10 });
    expect(requested(fetchMock).url).toBe("/api/v1/projects/people?page=2&size=10");
  });

  it("getPeople asks for a full page for name-resolution joins", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await getPeople();
    expect(requested(fetchMock).url).toBe("/api/v1/projects/people?page=1&size=200");
  });

  it("maps a wire person, including the roster UUID", async () => {
    stubFetch({
      items: [{ id: "p01", uuid: "uuid-1", name: "Nadia Rahman", role: "Head of Delivery", location: "Bengaluru", capacity_hours: 20 }],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    const [person] = await getPeople();
    expect(person).toEqual({
      id: "p01",
      uuid: "uuid-1",
      name: "Nadia Rahman",
      role: "Head of Delivery",
      location: "Bengaluru",
      capacity_hours: 20,
    });
  });
});

describe("createPerson", () => {
  it("POSTs the exact body to /projects/people", async () => {
    const fetchMock = stubFetch({
      id: "p20",
      uuid: "uuid-20",
      name: "New Hire",
      role: "",
      location: "",
      capacity_hours: 40,
    });
    const input = { ref: "p20", name: "New Hire", role: "", location: "", capacity_hours: 40 };
    await createPerson(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects/people");
    expect(body).toEqual(input);
  });
});

describe("updatePerson", () => {
  it("PATCHes /projects/people/{ref}", async () => {
    const fetchMock = stubFetch({
      id: "p01",
      uuid: "uuid-1",
      name: "Nadia Rahman",
      role: "VP Delivery",
      location: "Bengaluru",
      capacity_hours: 20,
    });
    await updatePerson("p01", { role: "VP Delivery" });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects/people/p01");
    expect(body).toEqual({ role: "VP Delivery" });
  });
});

describe("deletePerson", () => {
  it("DELETEs /projects/people/{ref}", async () => {
    const fetchMock = stubFetch(null, 204);
    await deletePerson("p01");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects/people/p01");
  });
});

describe("listTimesheets", () => {
  it("requests the real current week when unset, not the pinned fixture week", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-21T10:00:00Z")); // a Monday
    vi.resetModules();
    const live = await import("./client");
    const fetchMock = stubFetch(ENVELOPE);
    await live.listTimesheets();
    expect(requested(fetchMock).url).toBe("/api/v1/timesheets?week=2026-09-21&page=1&size=200");
  });

  it("passes an explicit week and status filter through", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listTimesheets({ week: "2026-08-24", status: "submitted" });
    expect(requested(fetchMock).url).toBe(
      "/api/v1/timesheets?week=2026-08-24&status=submitted&page=1&size=200",
    );
  });

  it("maps a wire row including its entries, not only the summed cells", async () => {
    stubFetch({
      items: [
        {
          person_id: "p01",
          week_start: "2026-08-24",
          status: "submitted",
          cells: [{ date: "2026-08-24", entered_hours: 8, billable_hours: 6.5 }],
          entries: [{ date: "2026-08-24", project_ref: "PB-01", entered_hours: 8, billable_hours: 6.5 }],
        },
      ],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    const result = await listTimesheets({ week: "2026-08-24" });
    expect(result.items[0]).toEqual({
      person_id: "p01",
      week_start: "2026-08-24",
      status: "submitted",
      cells: [{ date: "2026-08-24", entered_hours: 8, billable_hours: 6.5 }],
      entries: [{ date: "2026-08-24", project_ref: "PB-01", entered_hours: 8, billable_hours: 6.5 }],
    });
  });
});

describe("recordTimesheetWeek", () => {
  it("PUTs the exact body to /timesheets", async () => {
    const fetchMock = stubFetch({
      person_id: "p01",
      week_start: "2026-08-24",
      status: "draft",
      cells: [],
      entries: [],
    });
    const input = {
      person_ref: "p01",
      person_id: "uuid-1",
      week_start: "2026-08-24",
      status: "draft" as const,
      capacity_hours: 40,
      entries: [{ date: "2026-08-24", project_ref: "PB-01", entered_hours: 7.5, billable_hours: 7 }],
    };
    await recordTimesheetWeek(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PUT");
    expect(url).toBe("/api/v1/timesheets");
    expect(body).toEqual(input);
  });
});

describe("setTimesheetStatus", () => {
  it("POSTs the exact body to /timesheets/status", async () => {
    const fetchMock = stubFetch({
      person_id: "p01",
      week_start: "2026-08-24",
      status: "submitted",
      cells: [],
      entries: [],
    });
    const input = { person_ref: "p01", week_start: "2026-08-24", status: "submitted" as const };
    await setTimesheetStatus(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/timesheets/status");
    expect(body).toEqual(input);
  });
});

describe("getTimesheetSummary", () => {
  it("requests /timesheets/summary with the week", async () => {
    const fetchMock = stubFetch({
      week_start: "2026-08-24",
      entered_hours: 0,
      billable_hours: 0,
      capacity_hours: 0,
      utilization_pct: 0,
      series: [],
    });
    await getTimesheetSummary("2026-08-24");
    expect(requested(fetchMock).url).toBe("/api/v1/timesheets/summary?week=2026-08-24");
  });
});

describe("listStandups", () => {
  it("requests the day's check-ins with the blocked filter", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listStandups({ date: "2026-08-24", blocked: true });
    expect(requested(fetchMock).url).toBe("/api/v1/standups?date=2026-08-24&blocked=true&page=1&size=200");
  });
});

describe("postStandup", () => {
  it("POSTs the exact body to /standups", async () => {
    const fetchMock = stubFetch({
      id: "s01",
      person_id: "p02",
      date: "2026-08-24",
      yesterday: "y",
      today: "t",
      blockers: null,
    });
    const input = {
      person_ref: "p02",
      person_id: "uuid-2",
      date: "2026-08-24",
      yesterday: "y",
      today: "t",
      blockers: null,
    };
    await postStandup(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/standups");
    expect(body).toEqual(input);
  });
});
