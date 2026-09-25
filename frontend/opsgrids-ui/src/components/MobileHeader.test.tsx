import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MobileHeader } from "./MobileHeader";

describe("MobileHeader", () => {
  it("sits inside a header landmark", () => {
    render(
      <MobileHeader
        logo={<span>Mark</span>}
        productName="OpsGrids Revenue"
        onOpenNav={vi.fn()}
        profile={<span>Profile</span>}
      />,
    );
    expect(screen.getByRole("banner")).toBeTruthy();
  });

  it("opens the nav drawer from the menu button", async () => {
    const user = userEvent.setup();
    const onOpenNav = vi.fn();
    render(
      <MobileHeader
        logo={<span>Mark</span>}
        productName="OpsGrids Revenue"
        onOpenNav={onOpenNav}
        profile={<span>Profile</span>}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(onOpenNav).toHaveBeenCalledTimes(1);
  });

  it("shows no search control when the app passes none", () => {
    render(
      <MobileHeader
        logo={<span>Mark</span>}
        productName="OpsGrids Revenue"
        onOpenNav={vi.fn()}
        profile={<span>Profile</span>}
      />,
    );
    expect(screen.queryByRole("button", { name: /search/i })).toBeNull();
  });

  it("opens the command menu from the search trigger", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <MobileHeader
        logo={<span>Mark</span>}
        productName="OpsGrids Revenue"
        onOpenNav={vi.fn()}
        search={{ placeholder: "Search deals", onOpen }}
        profile={<span>Profile</span>}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Search deals" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
