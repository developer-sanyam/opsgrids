/**
 * The point of a shared status badge is that a word cannot look different in two places. That is
 * pinned here: the registry is closed over five tones, the brief's words are all in it, loose input
 * lands on the same tone as the canonical word, and the component offers no way to override the
 * registry for a registered word.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { isStatusWord, STATUS_TONES, STATUS_WORDS, statusLabel, statusTone } from "../lib/status";
import { StatusBadge } from "./StatusBadge";

describe("the status registry", () => {
  it("maps every word onto one of the five closed tones", () => {
    for (const word of STATUS_WORDS) {
      expect(STATUS_TONES).toContain(statusTone(word));
    }
  });

  it("holds every word the owner brief lists, with the mockups' colours", () => {
    expect({
      active: statusTone("active"),
      inactive: statusTone("inactive"),
      pending: statusTone("pending"),
      processing: statusTone("processing"),
      completed: statusTone("completed"),
      failed: statusTone("failed"),
      suspended: statusTone("suspended"),
      trial: statusTone("trial"),
      cancelled: statusTone("cancelled"),
      overdue: statusTone("overdue"),
    }).toEqual({
      active: "success",
      inactive: "neutral",
      pending: "warning",
      processing: "info",
      completed: "success",
      failed: "danger",
      suspended: "danger",
      trial: "warning",
      cancelled: "danger",
      overdue: "danger",
    });
  });

  it("orders priority low → urgent from info to danger", () => {
    expect(["low", "medium", "high", "urgent"].map((word) => statusTone(word))).toEqual([
      "info",
      "warning",
      "danger",
      "danger",
    ]);
  });

  it("reads a word however an API or a person wrote it", () => {
    expect(statusTone("On hold")).toBe("warning");
    expect(statusTone("on-hold")).toBe("warning");
    expect(statusTone(" ON_HOLD ")).toBe("warning");
    expect(statusTone("Past due")).toBe(statusTone("past_due"));
  });

  it("does not guess at a word it does not know", () => {
    expect(statusTone("awaiting_dns")).toBeUndefined();
    expect(isStatusWord("awaiting_dns")).toBe(false);
    expect(isStatusWord("on_hold")).toBe(true);
  });

  it("lists each word once, in canonical form", () => {
    expect(new Set(STATUS_WORDS).size).toBe(STATUS_WORDS.length);
    for (const word of STATUS_WORDS) expect(word).toMatch(/^[a-z]+(_[a-z]+)*$/);
  });

  it("labels a word in sentence case by default", () => {
    expect(statusLabel("on_hold")).toBe("On hold");
    expect(statusLabel("active")).toBe("Active");
  });
});

describe("StatusBadge", () => {
  it("shows a registered word in its registered tone", () => {
    render(<StatusBadge status="trial" />);
    const badge = screen.getByText("Trial").parentElement;
    expect(badge?.getAttribute("data-tone")).toBe("warning");
  });

  it("gives the same word the same tone, whatever label a product puts on it", () => {
    render(
      <>
        <StatusBadge status="past_due" />
        <StatusBadge status="past_due" label="Payment late" />
      </>,
    );
    const tones = [screen.getByText("Past due"), screen.getByText("Payment late")].map((label) =>
      label.parentElement?.getAttribute("data-tone"),
    );
    expect(tones).toEqual(["warning", "warning"]);
    expect(screen.getByText("Past due").parentElement?.className).toBe(
      screen.getByText("Payment late").parentElement?.className,
    );
  });

  it("places a product's own word on the tone the product chooses", () => {
    render(<StatusBadge tone="info" label="Awaiting DNS" />);
    expect(screen.getByText("Awaiting DNS").parentElement?.getAttribute("data-tone")).toBe("info");
  });

  it("is plain text, not a live region", () => {
    render(<StatusBadge status="failed" />);
    const badge = screen.getByText("Failed").parentElement;
    expect(badge?.getAttribute("role")).toBeNull();
    expect(badge?.getAttribute("aria-live")).toBeNull();
  });

  it("refuses, at compile time, an unknown word or an override of a known one", () => {
    // @ts-expect-error — not a registered word; a product word takes `tone` and `label`.
    const unknown = <StatusBadge status="awaiting_dns" />;
    // @ts-expect-error — a registered word's tone comes from the registry, never from the caller.
    const overridden = <StatusBadge status="active" tone="danger" />;
    // @ts-expect-error — a product word needs its label.
    const unlabelled = <StatusBadge tone="info" />;
    expect([unknown, overridden, unlabelled]).toHaveLength(3);
  });
});
