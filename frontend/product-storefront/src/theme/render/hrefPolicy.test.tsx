/**
 * The href policy where it actually lands: on the three surfaces that render a merchant-typed
 * destination.
 *
 * The allow-list itself is unit-tested in `linkList.test.ts`. What is pinned here is that each
 * surface *goes through it* — a policy that exists but that one renderer skips is not a policy —
 * and, just as importantly, that the ordinary destinations still work. A fix strict enough to kill
 * the contact block's `tel:` and `mailto:` links would be its own defect, so every refusal test
 * below is paired with the legitimate case it must not take with it.
 */

import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { defaultSettings } from "../sections";
import type { SettingValue } from "../schema";
import { FooterColumns } from "./FooterColumns";
import { ActionLink } from "./primitives";
import { TileShell } from "./TileShell";

const HOSTILE = "javascript:alert(1)";

/** The dev console line is asserted in `linkList.test.ts`; here it is only noise. */
beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

function inRouter(node: ReactNode) {
  return render(<MemoryRouter initialEntries={["/store"]}>{node}</MemoryRouter>);
}

/** Every anchor the render produced, so a refusal can be checked against the whole subtree. */
function anchors(container: HTMLElement): HTMLAnchorElement[] {
  return [...container.querySelectorAll("a")];
}

describe("a section's call to action", () => {
  it("renders no anchor at all for a refused destination", () => {
    const { container } = inRouter(<ActionLink href={HOSTILE}>Shop the collection</ActionLink>);

    expect(anchors(container)).toHaveLength(0);
    expect(screen.queryByRole("link")).toBeNull();
    // The merchant's words stay on the page: their call to action has gone inert, which is a thing
    // they can see, rather than disappearing without trace.
    expect(screen.getByText("Shop the collection")).not.toBeNull();
  });

  it("refuses the whole family, not just the one spelling", () => {
    for (const href of [
      "JaVaScRiPt:alert(1)",
      " javascript:alert(1)",
      "java\tscript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "vbscript:msgbox(1)",
      "//cdn.example.com/x",
      "#",
    ]) {
      const { container, unmount } = inRouter(<ActionLink href={href}>Enquire</ActionLink>);
      expect(anchors(container), href).toHaveLength(0);
      unmount();
    }
  });

  it("still routes an internal path through the router", () => {
    const { container } = inRouter(
      <ActionLink href="/store/catalogue">Shop the collection</ActionLink>,
    );

    expect(anchors(container)[0]?.getAttribute("href")).toBe("/store/catalogue");
  });

  it("still sends a mailto: and a tel: out as plain anchors", () => {
    const { container: mail } = inRouter(<ActionLink href="mailto:hello@example.in">Email us</ActionLink>);
    expect(anchors(mail)[0]?.getAttribute("href")).toBe("mailto:hello@example.in");

    const { container: phone } = inRouter(<ActionLink href="tel:+912912440000">Call us</ActionLink>);
    expect(anchors(phone)[0]?.getAttribute("href")).toBe("tel:+912912440000");
  });

  it("still links out to an absolute address", () => {
    const { container } = inRouter(
      <ActionLink href="https://instagram.com">See our work</ActionLink>,
    );

    expect(anchors(container)[0]?.getAttribute("href")).toBe("https://instagram.com");
  });
});

describe("a merchant-authored tile", () => {
  it("falls back to the same non-control a tile with no destination gets", () => {
    const { container } = inRouter(
      <TileShell href={HOSTILE} style={{}}>
        <span>Lighting</span>
      </TileShell>,
    );

    expect(anchors(container)).toHaveLength(0);
    // The image and title a merchant authored are still there; only the tap is gone.
    expect(screen.getByText("Lighting")).not.toBeNull();
    expect(container.firstElementChild?.tagName).toBe("DIV");
  });

  it("still routes a path and still leaves a tel: alone", () => {
    const { container: internal } = inRouter(
      <TileShell href="/store/catalogue?category=lighting" style={{}}>
        <span>Lighting</span>
      </TileShell>,
    );
    expect(anchors(internal)[0]?.getAttribute("href")).toBe("/store/catalogue?category=lighting");

    const { container: phone } = inRouter(
      <TileShell href="tel:+912912440000" style={{}}>
        <span>Call the showroom</span>
      </TileShell>,
    );
    expect(anchors(phone)[0]?.getAttribute("href")).toBe("tel:+912912440000");
  });
});

describe("a footer link column", () => {
  function renderFooter(overrides: Record<string, SettingValue>) {
    return render(
      <MemoryRouter initialEntries={["/store"]}>
        <FooterColumns
          settings={{ ...defaultSettings("footer-columns"), ...overrides }}
          products={[]}
          categories={[]}
          stacked={false}
          width={1200}
          mode="retail"
        />
      </MemoryRouter>,
    );
  }

  it("reads a refused line as text, exactly as it reads a line with no destination", () => {
    renderFooter({ col1Title: "Shop", col1Links: `Offers | ${HOSTILE}` });

    expect(screen.getByText("Offers")).not.toBeNull();
    expect(screen.queryByRole("link", { name: "Offers" })).toBeNull();
  });

  it("puts no javascript: anywhere in the document, whichever field carried it", () => {
    const { container } = renderFooter({
      col1Links: `Offers | ${HOSTILE}`,
      col2Links: "Careers | JaVaScRiPt:alert(1)",
      col3Links: "Mirror | //cdn.example.com/x",
    });

    for (const anchor of anchors(container)) {
      expect(anchor.getAttribute("href")?.toLowerCase() ?? "").not.toContain("javascript:");
    }
  });

  it("keeps the contact column's phone number and email working", () => {
    renderFooter({
      col4Title: "Reach us",
      col4Links:
        "+91 291 244 0000 | tel:+912912440000\nhello@example.in | mailto:hello@example.in\nMon to Sat, 10am to 7pm",
    });

    expect(screen.getByRole("link", { name: "+91 291 244 0000" }).getAttribute("href")).toBe(
      "tel:+912912440000",
    );
    expect(screen.getByRole("link", { name: "hello@example.in" }).getAttribute("href")).toBe(
      "mailto:hello@example.in",
    );
    expect(screen.getByText("Mon to Sat, 10am to 7pm")).not.toBeNull();
  });
});
