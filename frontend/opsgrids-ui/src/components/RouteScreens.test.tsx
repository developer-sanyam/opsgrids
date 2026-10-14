import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { lazyRoute } from "../lib/lazyRoute";
import { ComingSoonPage, NotFoundScreen, PermissionDenied, RouteFallback } from "./RouteScreens";

function renderInRouter(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>);
}

describe("NotFoundScreen", () => {
  it("says the page does not exist and offers the way back to the overview", () => {
    renderInRouter(<NotFoundScreen />);
    expect(screen.getByText("Not found")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "That page doesn't exist" })).toBeTruthy();
    expect(screen.getByText("Check the address, or head back to the overview.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to overview" }).getAttribute("href")).toBe("/");
  });

  it("takes an app's own sentence and way back", () => {
    renderInRouter(<NotFoundScreen description="Head back to your tasks." backTo="/tasks" backLabel="Back to tasks" />);
    expect(screen.getByText("Head back to your tasks.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to tasks" }).getAttribute("href")).toBe("/tasks");
  });
});

describe("ComingSoonPage", () => {
  it("promises a feature by default and names the surface", () => {
    renderInRouter(<ComingSoonPage title="Reports" description="Arrives later." />);
    expect(screen.getByText("Coming soon")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Reports" })).toBeTruthy();
  });

  it("swaps the eyebrow when the page is a 404 rather than a promise", () => {
    renderInRouter(<ComingSoonPage eyebrow="Not found" title="No such page" description="Check the address." />);
    expect(screen.queryByText("Coming soon")).toBeNull();
    expect(screen.getByText("Not found")).toBeTruthy();
  });
});

describe("PermissionDenied", () => {
  it("carries the screen's name as the h1 and names what cannot be opened", () => {
    render(<PermissionDenied what="campaigns" />);
    expect(screen.getByRole("heading", { level: 1, name: "Campaigns" })).toBeTruthy();
    expect(screen.getByText("You can't open campaigns right now")).toBeTruthy();
    expect(screen.getByText(/someone who manages roles in your organization/)).toBeTruthy();
  });

  it("takes an app's own remedy", () => {
    render(<PermissionDenied what="tasks" remedy="ask your supervisor to add the permission." />);
    expect(screen.getByText(/ask your supervisor to add the permission\./)).toBeTruthy();
  });
});

describe("RouteFallback and lazyRoute", () => {
  it("renders skeleton blocks that hold the space", () => {
    const { container } = render(<RouteFallback />);
    expect(container.querySelectorAll(".animate-pulse").length).toBe(2);
  });

  it("builds a route object that wraps the element in a suspense boundary", () => {
    const route = lazyRoute("employees", <p>Employees</p>);
    expect(route.path).toBe("employees");
    render(<>{route.element}</>);
    expect(screen.getByText("Employees")).toBeTruthy();
  });
});
