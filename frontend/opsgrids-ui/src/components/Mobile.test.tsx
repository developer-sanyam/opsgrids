/**
 * The phone contract of the shared layer: a 44px target without a 44px control, a sheet that
 * clears the notch, a header whose extra actions fold into a menu, and an error that talks to a
 * person. The layout itself is measured by the sweep (`VISUAL-TESTING.md`); these pin the classes
 * and the markup it depends on.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { plainErrorMessage } from "../lib/errorText";
import { stubViewport } from "../test-media";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Drawer } from "./Drawer";
import { PageHeader } from "./PageHeader";
import { Pagination } from "./Pagination";
import { Pill } from "./Pill";
import { ErrorState, Skeleton } from "./States";
import { Switch } from "./Switch";
import { Tabs } from "./Tabs";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("44px hit areas", () => {
  it("gives the compact controls a hit-44 target and leaves the 44px ones alone", () => {
    render(
      <>
        <Button size="sm">Small</Button>
        <Button size="md">Medium</Button>
        <Pill>Open</Pill>
        <Switch checked={false} onChange={() => undefined} label="Alerts" />
      </>,
    );
    expect(screen.getByRole("button", { name: "Small" }).className).toContain("hit-44");
    expect(screen.getByRole("button", { name: "Medium" }).className).not.toContain("hit-44");
    expect(screen.getByRole("button", { name: "Open" }).className).toContain("hit-44");
    expect(screen.getByRole("switch").className).toContain("hit-44");
  });

  it("lets a long label wrap and the button grow, instead of spilling out of a fixed height", () => {
    render(<Button>Import / Export</Button>);
    const classes = screen.getByRole("button").className;
    expect(classes).toContain("min-h-11");
    expect(classes).toContain("max-w-full");
    expect(classes).not.toMatch(/(^|\s)h-11(\s|$)/);
  });

  it("makes the dialog and drawer close controls 44px targets", () => {
    render(
      <>
        <Dialog open onClose={() => undefined} title="Rename">
          body
        </Dialog>
        <Drawer open onClose={() => undefined} title="Filters">
          body
        </Drawer>
      </>,
    );
    expect(screen.getByRole("button", { name: "Close dialog" }).className).toContain("hit-44");
    expect(screen.getByRole("button", { name: "Close Filters" }).className).toContain("hit-44");
  });
});

describe("sheets", () => {
  it("pads a full-screen dialog and a bottom drawer for the safe area", () => {
    render(
      <>
        <Dialog open onClose={() => undefined} title="Rename" footer={<Button>Save</Button>}>
          body
        </Dialog>
        <Drawer open onClose={() => undefined} title="Filters">
          body
        </Drawer>
      </>,
    );
    expect(screen.getByRole("dialog", { name: "Rename" }).className).toContain("sheet-safe");
    expect(screen.getByRole("dialog", { name: "Filters" }).className).toContain("sheet-safe-bottom");
  });

  it("keeps a dialog's actions on one row, the primary last", () => {
    render(
      <Dialog open onClose={() => undefined} title="Rename" footer={<Button>Save</Button>}>
        body
      </Dialog>,
    );
    const footer = screen.getByRole("button", { name: "Save" }).parentElement;
    expect(footer?.className).toContain("flex-row");
    expect(footer?.className).toContain("justify-end");
  });
});

describe("Pagination on a phone", () => {
  it("says where you are and offers Previous and Next as the 44px targets", () => {
    render(<Pagination page={3} pages={9} total={90} size={10} noun="widgets" onPageChange={() => undefined} />);
    expect(screen.getByText("Page 3 of 9")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous page" }).className).toContain("size-11");
    expect(screen.getByRole("button", { name: "Next page" }).className).toContain("size-11");
    // The numbered buttons exist for tablets and up only.
    expect(screen.getByRole("button", { name: "Page 4" }).className).toContain("hidden");
  });
});

describe("Tabs", () => {
  it("scrolls sideways with an edge fade and gives every tab a 44px width", () => {
    render(
      <Tabs
        label="Sections"
        items={[
          { id: "a", label: "A" },
          { id: "b", label: "B" },
        ]}
        value="a"
        onChange={() => undefined}
      />,
    );
    const list = screen.getByRole("tablist");
    expect(list.className).toContain("scroll-fade");
    expect(list.className).toContain("overflow-x-auto");
    expect(screen.getByRole("tab", { name: "A" }).className).toContain("min-w-11");
  });
});

describe("PageHeader secondary actions", () => {
  const items = [
    { label: "Import", onSelect: vi.fn() },
    { label: "Export", onSelect: vi.fn() },
  ];

  it("shows them as buttons from 640px", () => {
    stubViewport(1024);
    render(<PageHeader title="Employees" secondaryActions={items} actions={<Button>Add employee</Button>} />);
    expect(screen.getByRole("button", { name: "Import" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Export" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "More actions" })).toBeNull();
  });

  it("folds them into one More actions menu on a phone, the primary action kept", async () => {
    stubViewport(375);
    const user = userEvent.setup();
    render(<PageHeader title="Employees" secondaryActions={items} actions={<Button>Add employee</Button>} />);
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add employee" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Export" }));
    expect(items[1]?.onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("loading and error wording", () => {
  it("announces a labelled Skeleton and keeps a bare one silent", () => {
    render(
      <>
        <Skeleton label="Loading invoices" />
        <div data-testid="bare">
          <Skeleton />
        </div>
      </>,
    );
    expect(screen.getByRole("status").textContent).toBe("Loading invoices");
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("bare").querySelector("[aria-hidden]")).not.toBeNull();
  });

  it("turns an HTTP status into words a person can use", () => {
    expect(plainErrorMessage("Request failed with 500")).toMatch(/our side/);
    expect(plainErrorMessage("Request failed with status code 403")).toMatch(/don't have access/);
    expect(plainErrorMessage("Request failed with 404")).toMatch(/couldn't find/);
    expect(plainErrorMessage("Your connection dropped.")).toBe("Your connection dropped.");
  });

  it("shows the plain wording in an ErrorState", () => {
    render(<ErrorState message="Request failed with 500" />);
    expect(screen.queryByText(/Request failed with 500/)).toBeNull();
    expect(screen.getByText(/our side/)).toBeTruthy();
  });
});
