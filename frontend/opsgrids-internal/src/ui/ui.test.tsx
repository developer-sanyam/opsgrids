/**
 * The console's own small components — the ones that exist because they are this app's alone: a
 * lifecycle pill, a money figure, the environment marker, the two charts and the way a failed
 * read is told apart. Each is a piece an operator reads without thinking about, which is why what
 * gets pinned is what they must never get wrong: meaning that rests on words as well as colour,
 * and a figure that is always an integer amount in a currency.
 */

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import { ContractMismatchError } from "@/api/shape";
import type { BreakdownSegment } from "@/api/types";
import { OperatorAuthProvider } from "@/auth/OperatorAuthProvider";
import { operatorWith } from "@/testing-render";
import { BreakdownBar } from "./BreakdownBar";
import { EnvBadge } from "./EnvBadge";
import { LoadFailure } from "./LoadFailure";
import { Money } from "./Money";
import { MrrTrend } from "./MrrTrend";
import { PermissionDenied, RequireCapability } from "./PermissionDenied";
import { RouteError } from "./RouteError";
import {
  AccountStatusPill,
  BespokeTag,
  CompTag,
  InvoiceStatePill,
  LedgerKindPill,
  PlanVersionPill,
  StatusPill,
} from "./StatusPill";

describe("status pills", () => {
  it("say the lifecycle state in words, with a dot that is decoration and not the message", () => {
    for (const [status, label] of [
      ["active", "Active"],
      ["trial", "Trial"],
      ["past_due", "Past due"],
      ["suspended", "Suspended"],
      ["cancelled", "Cancelled"],
    ] as const) {
      const { container, unmount } = render(<AccountStatusPill status={status} />);
      expect(container.textContent).toBe(label);
      expect(container.querySelector("[aria-hidden]")).not.toBeNull();
      unmount();
    }
  });

  it("map each lifecycle state onto the Console's semantic colours, not a palette of their own", () => {
    const tone = (status: "active" | "trial" | "past_due" | "suspended" | "cancelled") => {
      const { container, unmount } = render(<AccountStatusPill status={status} />);
      const classes = container.firstElementChild?.className ?? "";
      unmount();
      return classes;
    };
    expect(tone("active")).toContain("text-present");
    expect(tone("trial")).toContain("text-info");
    expect(tone("past_due")).toContain("text-late");
    expect(tone("suspended")).toContain("text-danger");
    expect(tone("cancelled")).toContain("text-muted");
  });

  it("say what a plan version, an invoice and a ledger entry are", () => {
    const labels = [
      render(<PlanVersionPill state="grandfathered" />).container.textContent,
      render(<InvoiceStatePill state="finalized" />).container.textContent,
      render(<LedgerKindPill kind="reversal" />).container.textContent,
    ];
    expect(labels).toEqual(["Grandfathered", "Finalized", "Reversal"]);
  });

  it("carry the accent tone for a pill that is not a lifecycle state", () => {
    const { container } = render(<StatusPill tone="accent">Bespoke pricing</StatusPill>);
    expect(container.textContent).toBe("Bespoke pricing");
    expect(container.firstElementChild?.className).toContain("text-accent-ink");
  });

  it("tag bespoke pricing and complimentary access in words", () => {
    expect(render(<BespokeTag />).container.textContent).toContain("Bespoke");
    expect(render(<CompTag />).container.textContent).toBe("Comp");
  });
});

describe("Money", () => {
  it("renders an integer minor amount as grouped rupees in the figure face", () => {
    const { container } = render(<Money minor={45_000_000} currency="INR" />);
    expect(container.textContent).toBe("₹4,50,000");
    expect(container.firstElementChild?.className).toContain("money");
  });

  it("tints a negative amount only when asked, and the sign is in the text either way", () => {
    const plain = render(<Money minor={-500_00} currency="INR" />).container;
    const signed = render(<Money minor={-500_00} currency="INR" signed />).container;
    expect(plain.textContent).toBe("-₹500");
    expect(signed.textContent).toBe("-₹500");
    expect(plain.firstElementChild?.className).not.toContain("text-negative");
    expect(signed.firstElementChild?.className).toContain("text-negative");
  });

  it("never tints a positive one", () => {
    const { container } = render(<Money minor={500_00} currency="INR" signed />);
    expect(container.firstElementChild?.className).not.toContain("text-negative");
  });

  it("shows fraction digits on request, and a compact figure for a headline", () => {
    expect(render(<Money minor={1_999_99} currency="INR" precise />).container.textContent).toBe("₹1,999.99");
    expect(render(<Money minor={184_000_000} currency="INR" compact />).container.textContent).toBe("₹18L");
  });
});

describe("EnvBadge", () => {
  it("says the console is on fixtures, in words, in a test that runs on them", () => {
    render(<EnvBadge />);
    expect(screen.getByText("Fixtures data")).toBeDefined();
    expect(screen.getByText("Fixtures data").closest("span")?.getAttribute("title")).toMatch(/not a live read/);
  });
});

describe("BreakdownBar", () => {
  const SEGMENTS: BreakdownSegment[] = [
    { key: "a", label: "Converted", count: 8, tone: "active" },
    { key: "b", label: "Still in trial", count: 3, tone: "trial" },
    { key: "c", label: "Expired", count: 0, tone: "cancelled" },
    { key: "d", label: "Lapsed", count: 1, tone: "pastdue" },
  ];

  it("reads the whole breakdown as one image, and each segment with its count and share in the legend", () => {
    render(<BreakdownBar segments={SEGMENTS} unit="accounts" />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe(
      "Converted: 8 accounts, Still in trial: 3 accounts, Expired: 0 accounts, Lapsed: 1 accounts",
    );
    const legend = screen.getByRole("list");
    expect(within(legend).getByText("Converted").parentElement?.textContent).toContain("67%");
    expect(within(legend).getByText("Still in trial").parentElement?.textContent).toContain("25%");
  });

  it("draws no segment for a count of nothing, and sizes the others by a custom property", () => {
    const { container } = render(<BreakdownBar segments={SEGMENTS} unit="accounts" />);
    const drawn = container.querySelectorAll<HTMLElement>("[role='img'] > span");
    expect(drawn).toHaveLength(3);
    expect(drawn[0]?.style.getPropertyValue("--share")).toBe(`${(8 / 12) * 100}%`);
    // No raw width, colour or size is set inline — only the custom property a class consumes.
    for (const segment of drawn) expect(segment.getAttribute("style")).toMatch(/^--share:/);
  });

  it("does not divide by zero for an all-zero breakdown", () => {
    render(<BreakdownBar segments={[{ key: "a", label: "None", count: 0, tone: "active" }]} unit="accounts" />);
    expect(screen.getByRole("list").textContent).toContain("0%");
    expect(screen.getByRole("list").textContent).not.toContain("NaN");
  });
});

describe("MrrTrend", () => {
  const SERIES = [
    { label: "Jun", mrr_minor: 1_000_000 },
    { label: "Jul", mrr_minor: 1_500_000 },
    { label: "Aug", mrr_minor: 2_000_000 },
  ];

  it("is an image whose figures are also plain text, so the chart is not the only place they exist", () => {
    const { container } = render(<MrrTrend series={SERIES} currency="INR" />);
    expect(screen.getByRole("img", { name: "Monthly recurring revenue, trailing twelve months" })).toBeDefined();
    const caption = container.querySelector("figcaption");
    expect(caption?.textContent).toContain("Jun ₹10,000, Jul ₹15,000, Aug ₹20,000");
    expect(caption?.className).toContain("sr-only");
  });

  it("reads out a month's figure when the pointer is over it, and puts it away when it leaves", () => {
    const { container } = render(<MrrTrend series={SERIES} currency="INR" />);
    const chart = screen.getByRole("img");
    chart.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });

    fireEvent(chart, new MouseEvent("pointermove", { clientX: 100, bubbles: true }));
    const tooltip = container.querySelector(".pointer-events-none");
    expect(tooltip?.textContent).toContain("Jul");
    expect(tooltip?.textContent).toContain("₹15,000");
    // Positioned by two custom properties, not a left and a top set inline.
    expect(tooltip?.getAttribute("style")).toMatch(/^--x: [\d.]+%; --y: [\d.]+%;?$/);

    fireEvent(chart, new MouseEvent("pointerout", { bubbles: true, relatedTarget: document.body }));
    expect(container.querySelector(".pointer-events-none")).toBeNull();
  });

  it("holds the pointer to the ends of the series", () => {
    const { container } = render(<MrrTrend series={SERIES} currency="INR" />);
    const chart = screen.getByRole("img");
    chart.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });

    fireEvent(chart, new MouseEvent("pointermove", { clientX: 9999, bubbles: true }));
    expect(container.querySelector(".pointer-events-none")?.textContent).toContain("Aug");
    fireEvent(chart, new MouseEvent("pointermove", { clientX: -50, bubbles: true }));
    expect(container.querySelector(".pointer-events-none")?.textContent).toContain("Jun");
  });
});

describe("LoadFailure", () => {
  it("says a real failure is one, with what the server said and its reference, and offers to try again", async () => {
    const user = userEvent.setup();
    let retried = 0;
    render(
      <LoadFailure
        error={new ApiError(500, "directory unavailable", { status: 500, request_id: "abc-1" })}
        title="Couldn't load accounts"
        onRetry={() => {
          retried += 1;
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Couldn't load accounts");
    expect(alert.textContent).toContain("directory unavailable");
    expect(alert.textContent).toContain("Reference: abc-1");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(retried).toBe(1);
  });

  it("says a dropped connection is a failure with a sentence that is not machinery", () => {
    render(<LoadFailure error={new TypeError("Failed to fetch")} title="Couldn't load accounts" onRetry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toContain("Something went wrong. Try again.");
    expect(screen.getByRole("alert").textContent).not.toContain("Failed to fetch");
  });

  it("does not offer to retry a 403, and says it is a role — in the server's words", () => {
    render(
      <LoadFailure
        error={new ApiError(403, "This action needs one of these operator roles: owner", { status: 403 })}
        title="Couldn't load accounts"
        onRetry={() => undefined}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("You can't view this");
    expect(alert.textContent).toContain("This action needs one of these operator roles: owner");
    expect(within(alert).queryByRole("button")).toBeNull();
  });

  it("does not offer to retry a 404, and says it was not found", () => {
    render(
      <LoadFailure error={new ApiError(404, "No such organization", { status: 404, detail: "No such organization" })} title="x" onRetry={() => undefined} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Not found");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("names a shape the console does not read as one, and offers no retry that would ask the same question", () => {
    render(
      <LoadFailure
        error={new ContractMismatchError("/admin/organizations", ["items", "total"])}
        title="Couldn't load accounts"
        onRetry={() => undefined}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("This screen and the operator API are out of step");
    expect(alert.textContent).toContain("/admin/organizations");
    expect(within(alert).queryByRole("button")).toBeNull();
  });

  it("offers no retry when the screen gave none", () => {
    render(<LoadFailure error={new ApiError(500, "boom", { status: 500 })} title="Couldn't load" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("PermissionDenied and RequireCapability", () => {
  function inSession(roles: string[], children: React.ReactNode) {
    return render(
      <MemoryRouter>
        <OperatorAuthProvider fixtureOperator={operatorWith(roles)}>{children}</OperatorAuthProvider>
      </MemoryRouter>,
    );
  }

  it("is a whole page — an h1 naming the screen, then what is wrong — and names who may open it", () => {
    render(<PermissionDenied what="the audit log" capability="audit" />);
    expect(screen.getByRole("heading", { level: 1, name: "The audit log" })).toBeDefined();
    expect(screen.getByText("You can't open the audit log")).toBeDefined();
    expect(screen.getByText("Your operator role doesn't include the audit log. It is open to: Owner, Support. If you need it, ask an owner.")).toBeDefined();
  });

  it("renders its children for a role the capability admits", async () => {
    inSession(["support"], <RequireCapability capability="audit" what="the audit log"><p>The log</p></RequireCapability>);
    expect(await screen.findByText("The log")).toBeDefined();
    expect(screen.queryByText("You can't open the audit log")).toBeNull();
  });

  it("renders the denial, and none of the children, for a role it does not", async () => {
    inSession(["billing_ops"], <RequireCapability capability="audit" what="the audit log"><p>The log</p></RequireCapability>);
    expect(await screen.findByText("You can't open the audit log")).toBeDefined();
    expect(screen.queryByText("The log")).toBeNull();
  });
});

describe("RouteError", () => {
  it("says the screen could not be shown, with the error's sentence and no stack, and offers a reload", async () => {
    const { createMemoryRouter, RouterProvider } = await import("react-router-dom");
    function Broken(): never {
      throw new Error("expected a list, got nothing");
    }
    const router = createMemoryRouter([{ path: "/", element: <Broken />, errorElement: <RouteError /> }]);
    const spy = (await import("vitest")).vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<RouterProvider router={router} />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("This screen couldn't be shown");
    expect(alert.textContent).toContain("expected a list, got nothing");
    expect(alert.textContent).not.toMatch(/at Broken|\.tsx/);
    expect(screen.getByRole("button", { name: "Reload the page" })).toBeDefined();
    spy.mockRestore();
  });
});
