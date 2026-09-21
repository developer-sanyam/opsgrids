import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createMember,
  createMilestone,
  createProject,
  createTask,
  deleteMember,
  deleteMilestone,
  deleteProject,
  deleteTask,
  listMembers,
  listMilestones,
  listProjects,
  listTasks,
  updateMember,
  updateMilestone,
  updateProject,
  updateTask,
} from "./client";

const ENVELOPE = { items: [], total: 0, page: 1, size: 25, pages: 0 };

/** Stub `fetch` once, capture the request, and answer with the given JSON body. */
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

function requested(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!call) throw new Error("fetch was never called");
  const [url, init] = call;
  return {
    url,
    method: init?.method ?? "GET",
    body: init?.body ? JSON.parse(String(init.body)) : undefined,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listProjects", () => {
  it("pages through the real envelope with q, health, phase and sort", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listProjects({ q: "helios", health: "at_risk", phase: "design", sort: "name", page: 2, size: 10 });
    const { url, method } = requested(fetchMock);
    expect(method).toBe("GET");
    expect(url).toBe(
      "/api/v1/projects-workspace/projects?q=helios&health=at_risk&phase=design&sort=name&page=2&size=10",
    );
  });

  it("defaults to page 1 at the standard page size and omits unset filters", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listProjects();
    expect(requested(fetchMock).url).toBe("/api/v1/projects-workspace/projects?page=1&size=25");
  });
});

describe("createProject", () => {
  it("POSTs the exact body to /projects", async () => {
    const fetchMock = stubFetch({
      id: "p-1",
      code: "PRJ-200",
      name: "New Engagement",
      client: "",
      owner: { id: "", name: "Unassigned", role: "" },
      phase: "discovery",
      health: "on_track",
      progress_pct: 0,
      start_date: "2026-01-01",
      target_date: "2026-06-01",
      budget_minor: 0,
      spent_minor: 0,
      currency: "INR",
      team_size: 0,
      open_tasks: 0,
      milestone_count: 0,
      summary: "",
    });
    const input = {
      code: "PRJ-200",
      name: "New Engagement",
      start_date: "2026-01-01",
      target_date: "2026-06-01",
    };
    await createProject(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects-workspace/projects");
    expect(body).toEqual(input);
  });
});

describe("updateProject", () => {
  it("PATCHes only the given fields, and sends an explicit null to clear owner_ref", async () => {
    const fetchMock = stubFetch({
      id: "p-1",
      code: "PRJ-200",
      name: "Renamed",
      client: "",
      owner: { id: "", name: "Unassigned", role: "" },
      phase: "build",
      health: "on_track",
      progress_pct: 10,
      start_date: "2026-01-01",
      target_date: "2026-06-01",
      budget_minor: 0,
      spent_minor: 0,
      currency: "INR",
      team_size: 0,
      open_tasks: 0,
      milestone_count: 0,
      summary: "",
    });
    await updateProject("PRJ-200", { name: "Renamed", owner_ref: null });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects-workspace/projects/PRJ-200");
    expect(body).toEqual({ name: "Renamed", owner_ref: null });
  });
});

describe("deleteProject", () => {
  it("DELETEs by code", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteProject("PRJ-200");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects-workspace/projects/PRJ-200");
  });
});

describe("listMilestones", () => {
  it("sends status and project_code straight through", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listMilestones({ status: "at_risk", project_code: "PRJ-200", page: 1, size: 25 });
    expect(requested(fetchMock).url).toBe(
      "/api/v1/projects-workspace/milestones?status=at_risk&project_code=PRJ-200&page=1&size=25",
    );
  });
});

describe("createMilestone", () => {
  it("POSTs the exact body to /milestones", async () => {
    const fetchMock = stubFetch({
      id: "m-1",
      project_id: "p-1",
      project_code: "PRJ-200",
      project_name: "New Engagement",
      title: "Kickoff",
      owner: { id: "", name: "Unassigned", role: "" },
      due_date: "2026-02-01",
      status: "on_track",
      progress_pct: 0,
    });
    const input = { project_code: "PRJ-200", title: "Kickoff", due_date: "2026-02-01" };
    await createMilestone(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects-workspace/milestones");
    expect(body).toEqual(input);
  });
});

describe("updateMilestone", () => {
  it("PATCHes by id and sends an explicit null to clear due_date", async () => {
    const fetchMock = stubFetch({
      id: "m-1",
      project_id: "p-1",
      project_code: "PRJ-200",
      project_name: "New Engagement",
      title: "Kickoff",
      owner: { id: "", name: "Unassigned", role: "" },
      due_date: null,
      status: "on_track",
      progress_pct: 0,
    });
    await updateMilestone("m-1", { due_date: null });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects-workspace/milestones/m-1");
    expect(body).toEqual({ due_date: null });
  });
});

describe("deleteMilestone", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteMilestone("m-1");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects-workspace/milestones/m-1");
  });
});

describe("listTasks", () => {
  it("sends stage and project_code straight through", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listTasks({ stage: "review", project_code: "PRJ-200", page: 1, size: 25 });
    expect(requested(fetchMock).url).toBe(
      "/api/v1/projects-workspace/tasks?stage=review&project_code=PRJ-200&page=1&size=25",
    );
  });
});

describe("createTask", () => {
  it("POSTs the exact body to /tasks", async () => {
    const fetchMock = stubFetch({
      id: "t-1",
      project_id: "p-1",
      project_code: "PRJ-200",
      title: "Wire up auth",
      assignee: { id: "", name: "Unassigned", role: "" },
      stage: "backlog",
      priority: "medium",
      due_date: null,
      points: 0,
    });
    const input = { project_code: "PRJ-200", title: "Wire up auth" };
    await createTask(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects-workspace/tasks");
    expect(body).toEqual(input);
  });
});

describe("updateTask", () => {
  it("PATCHes a stage move — what a Kanban drag persists", async () => {
    const fetchMock = stubFetch({
      id: "t-1",
      project_id: "p-1",
      project_code: "PRJ-200",
      title: "Wire up auth",
      assignee: { id: "", name: "Unassigned", role: "" },
      stage: "in_progress",
      priority: "medium",
      due_date: null,
      points: 0,
    });
    await updateTask("t-1", { stage: "in_progress" });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects-workspace/tasks/t-1");
    expect(body).toEqual({ stage: "in_progress" });
  });
});

describe("deleteTask", () => {
  it("DELETEs by id", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteTask("t-1");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects-workspace/tasks/t-1");
  });
});

describe("listMembers", () => {
  it("sends q straight through", async () => {
    const fetchMock = stubFetch(ENVELOPE);
    await listMembers({ q: "aarav", page: 1, size: 25 });
    expect(requested(fetchMock).url).toBe("/api/v1/projects-workspace/members?q=aarav&page=1&size=25");
  });
});

describe("createMember", () => {
  it("POSTs the exact body to /members", async () => {
    const fetchMock = stubFetch({ id: "p-11", name: "New Person", role: "", directory_id: null });
    const input = { ref: "p-11", name: "New Person" };
    await createMember(input);
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("POST");
    expect(url).toBe("/api/v1/projects-workspace/members");
    expect(body).toEqual(input);
  });
});

describe("updateMember", () => {
  it("PATCHes by ref — ref itself never travels in the body", async () => {
    const fetchMock = stubFetch({ id: "p-11", name: "Renamed", role: "", directory_id: null });
    await updateMember("p-11", { name: "Renamed" });
    const { url, method, body } = requested(fetchMock);
    expect(method).toBe("PATCH");
    expect(url).toBe("/api/v1/projects-workspace/members/p-11");
    expect(body).toEqual({ name: "Renamed" });
  });
});

describe("deleteMember", () => {
  it("DELETEs by ref", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await deleteMember("p-11");
    const { url, method } = requested(fetchMock);
    expect(method).toBe("DELETE");
    expect(url).toBe("/api/v1/projects-workspace/members/p-11");
  });
});
