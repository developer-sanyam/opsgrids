/**
 * SLA state derivation, pinned against an injected `now` rather than the real clock — the bug
 * this guards against is a frozen `NOW` reaching production (`docs/loop/crud-audit/support.md`
 * gap 8): every one of these must answer correctly for *any* instant passed in, not just the one
 * the fixtures happen to be written around.
 */
import { describe, expect, it } from "vitest";

import type { Ticket } from "@/api/types";
import { formatDuration, needsAttention, slaInfo } from "./sla";

const BASE: Ticket = {
  id: "TK-0001",
  subject: "Test ticket",
  requester: { name: "A", email: "a@example.com", company: "Acme", kind: "customer" },
  priority: "normal",
  status: "open",
  channel: "web",
  assignee_id: null,
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  first_response_at: null,
  sla_due: null,
  sla_breached: false,
  satisfaction: null,
  messages: [],
};

describe("formatDuration", () => {
  it("renders minutes under an hour", () => {
    expect(formatDuration(42)).toBe("42m");
  });
  it("renders whole hours with no remainder", () => {
    expect(formatDuration(180)).toBe("3h");
  });
  it("renders hours and minutes together", () => {
    expect(formatDuration(190)).toBe("3h 10m");
  });
  it("takes the absolute value — a negative span reads the same as its magnitude", () => {
    expect(formatDuration(-42)).toBe("42m");
  });
});

describe("slaInfo", () => {
  it("is on_track well ahead of the deadline", () => {
    const ticket = { ...BASE, sla_due: "2026-01-01T04:00:00Z" };
    const now = new Date("2026-01-01T00:00:00Z");
    expect(slaInfo(ticket, now).state).toBe("on_track");
  });

  it("is due_soon inside the last hour", () => {
    const ticket = { ...BASE, sla_due: "2026-01-01T00:30:00Z" };
    const now = new Date("2026-01-01T00:00:00Z");
    const info = slaInfo(ticket, now);
    expect(info.state).toBe("due_soon");
    expect(info.label).toBe("Due in 30m");
  });

  it("is breached once the deadline has passed — a ticket 'due in 30m' one day does not read as", () => {
    // months overdue the next, because the clock advanced rather than staying frozen.
    const ticket = { ...BASE, sla_due: "2026-01-01T00:00:00Z" };
    const now = new Date("2026-01-01T00:30:00Z");
    const info = slaInfo(ticket, now);
    expect(info.state).toBe("breached");
    expect(info.label).toBe("Breached 30m ago");
  });

  it("is paused on hold, regardless of the deadline", () => {
    const ticket = { ...BASE, status: "on_hold" as const, sla_due: "2026-01-01T00:00:00Z" };
    expect(slaInfo(ticket, new Date("2026-06-01T00:00:00Z")).state).toBe("paused");
  });

  it("is met when solved without a breach, missed when solved with one", () => {
    const solved = { ...BASE, status: "solved" as const, sla_due: null };
    expect(slaInfo({ ...solved, sla_breached: false }).state).toBe("met");
    expect(slaInfo({ ...solved, sla_breached: true }).state).toBe("missed");
  });

  it("has no live clock once solved, or when no target is set", () => {
    expect(slaInfo({ ...BASE, status: "solved" as const }).minutes).toBeNull();
    expect(slaInfo({ ...BASE, sla_due: null }).minutes).toBeNull();
  });
});

describe("needsAttention", () => {
  it("is true only for breached or due-soon tickets", () => {
    const now = new Date("2026-01-01T00:00:00Z");
    expect(needsAttention({ ...BASE, sla_due: "2026-01-01T00:30:00Z" }, now)).toBe(true);
    expect(needsAttention({ ...BASE, sla_due: "2025-12-31T23:00:00Z" }, now)).toBe(true);
    expect(needsAttention({ ...BASE, sla_due: "2026-01-01T06:00:00Z" }, now)).toBe(false);
    expect(needsAttention({ ...BASE, status: "solved" as const, sla_breached: false }, now)).toBe(false);
  });
});
