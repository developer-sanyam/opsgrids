import { describe, expect, it } from "vitest";

import { INCIDENT_TRANSITIONS, transitionLabel } from "./incidentLifecycle";

describe("INCIDENT_TRANSITIONS", () => {
  it("mirrors the backend's map exactly", () => {
    expect(INCIDENT_TRANSITIONS).toEqual({
      new: ["in_progress", "on_hold", "resolved"],
      in_progress: ["on_hold", "resolved"],
      on_hold: ["in_progress", "resolved"],
      resolved: ["closed", "in_progress"],
      closed: [],
    });
  });

  it("closed is terminal", () => {
    expect(INCIDENT_TRANSITIONS.closed).toHaveLength(0);
  });
});

describe("transitionLabel", () => {
  it("calls new → in_progress 'Start work'", () => {
    expect(transitionLabel("new", "in_progress")).toBe("Start work");
  });

  it("calls on_hold → in_progress 'Resume'", () => {
    expect(transitionLabel("on_hold", "in_progress")).toBe("Resume");
  });

  it("calls resolved → in_progress 'Reopen'", () => {
    expect(transitionLabel("resolved", "in_progress")).toBe("Reopen");
  });

  it("always calls a move to resolved 'Resolve…'", () => {
    expect(transitionLabel("new", "resolved")).toBe("Resolve…");
    expect(transitionLabel("in_progress", "resolved")).toBe("Resolve…");
  });

  it("always calls a move to closed 'Close'", () => {
    expect(transitionLabel("resolved", "closed")).toBe("Close");
  });
});
