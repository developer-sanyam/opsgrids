import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ImsPush } from "@/api/types";
import { PushBadge } from "./PushBadge";

function push(over: Partial<ImsPush>): ImsPush {
  return {
    id: "push-1",
    decision_id: "dec-1",
    ims_document_id: "doc-1",
    period: "2026-08",
    action: "accept",
    version: 1,
    status: "queued",
    is_current: true,
    requested_at: null,
    attempts: 0,
    max_attempts: 6,
    next_attempt_at: null,
    pushed_at: null,
    error_code: null,
    error: null,
    portal: null,
    observed_action: null,
    observed_at: null,
    drift: false,
    drift_detected_at: null,
    ...over,
  };
}

describe("the push badge", () => {
  it("renders nothing for a document with no decision", () => {
    const { container } = render(<PushBadge push={null} />);
    expect(container.textContent).toBe("");
  });

  it("names the state in words — colour only agrees", () => {
    render(<PushBadge push={push({ status: "pushed", pushed_at: "2026-09-24T06:00:00Z" })} />);
    expect(screen.getByText("On GSTN")).toBeTruthy();
  });

  it("keeps the queue compact for a state nobody has to act on", () => {
    render(<PushBadge push={push({ status: "pushed" })} />);
    expect(screen.queryByText("GSTN holds this decision.")).toBeNull();
  });

  it("says why for a state a person has to look at, even when compact", () => {
    render(<PushBadge push={push({ status: "failed", error_code: "IMS201", error: "document not found" })} />);
    expect(screen.getByText("Send failed")).toBeTruthy();
    expect(screen.getByText("Code IMS201 — document not found")).toBeTruthy();
  });

  it("says what the state means in the decision history, where there is room", () => {
    render(<PushBadge push={push({ status: "pushed" })} detail="full" />);
    expect(screen.getByText("GSTN holds this decision.")).toBeTruthy();
  });

  it("shows drift as its own badge beside the push state, with the sentence", () => {
    render(
      <PushBadge
        push={push({ status: "pushed", drift: true, observed_action: "pending", action: "accept" })}
      />,
    );
    expect(screen.getByText("On GSTN")).toBeTruthy();
    expect(screen.getByText("GSTN differs")).toBeTruthy();
    expect(screen.getByText("You sent accepted; the last sync found GSTN showing on hold.")).toBeTruthy();
  });

  it("never reads a decision that has not been sent as drift", () => {
    render(<PushBadge push={push({ status: "queued", drift: false })} />);
    expect(screen.queryByText("GSTN differs")).toBeNull();
  });
});
