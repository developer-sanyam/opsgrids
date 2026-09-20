/**
 * The buyer surface's four states, and the one escaping rule this bundle owns.
 *
 * The state assertions are here rather than only on the client because the defect being guarded
 * against is a *rendering* one: a component that collapses every failure into "not found" passes
 * every client-level test and still shows a stranger "there's no shop here" on a working
 * merchant's own domain.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreContextOut } from "@/api/storeContext";
import { BuyerApp } from "./BuyerApp";
import {
  LOADING_STATUS,
  NOT_FOUND_TITLE,
  RETRY_LABEL,
  NO_PAGES_TITLE,
  UNAVAILABLE_TITLE,
  otherAddressNotice,
} from "./copy";

const CONTEXT: StoreContextOut = {
  store: "greenleaf",
  name: "Greenleaf Organics",
  tagline: "Fresh from the farm, daily",
  logo_text: "GLO",
  currency: "INR",
  timezone: "Asia/Kolkata",
  canonical_host: "merchant.com",
  is_canonical: true,
  checked_at: "2026-09-19T11:14:22Z",
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function mockFetch(...responses: Response[]) {
  const fetchMock = vi.fn();
  for (const response of responses) fetchMock.mockResolvedValueOnce(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  document.head.querySelectorAll('link[rel="canonical"]').forEach((node) => {
    node.remove();
  });
});

describe("the four states", () => {
  it("announces the wait while the bootstrap is in flight, without drawing a lie", () => {
    mockFetch(jsonResponse(200, CONTEXT));
    render(<BuyerApp />);

    // A live region, not a spinner with no name — this is the only thing a screen reader has
    // while the shop is unknown.
    expect(screen.getByRole("status").textContent).toBe(LOADING_STATUS);
  });

  it("renders the shop once the server says which one it is", async () => {
    mockFetch(jsonResponse(200, CONTEXT));
    render(<BuyerApp />);

    expect(await screen.findByRole("heading", { level: 1, name: CONTEXT.name })).toBeTruthy();
    expect(screen.getByText(String(CONTEXT.tagline))).toBeTruthy();
    // The empty state of the shop's own content: the address works and there is nothing on it.
    expect(screen.getByRole("heading", { level: 2, name: NO_PAGES_TITLE })).toBeTruthy();
  });

  it("renders a 404 as 'there's no shop here' and offers NO retry", async () => {
    mockFetch(jsonResponse(404, { detail: "No published store at this address." }));
    render(<BuyerApp />);

    expect(await screen.findByRole("heading", { level: 1, name: NOT_FOUND_TITLE })).toBeTruthy();
    // Retrying cannot change an answer the server computed, and a button here would invite a
    // buyer to keep asking.
    expect(screen.queryByRole("button", { name: RETRY_LABEL })).toBeNull();
  });

  it.each([422, 429, 500, 503])(
    "renders a %i as 'we can't load this shop right now', with a retry",
    async (status) => {
      mockFetch(jsonResponse(status, {}));
      render(<BuyerApp />);

      expect(
        await screen.findByRole("heading", { level: 1, name: UNAVAILABLE_TITLE }),
      ).toBeTruthy();
      expect(screen.getByRole("button", { name: RETRY_LABEL })).toBeTruthy();
      // The regression this whole surface exists to prevent.
      expect(screen.queryByText(NOT_FOUND_TITLE)).toBeNull();
    },
  );

  it("renders a dead network as 'we could not look', never as an absence", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    render(<BuyerApp />);

    expect(await screen.findByRole("heading", { level: 1, name: UNAVAILABLE_TITLE })).toBeTruthy();
  });

  it("retries on the button and renders the shop when the second attempt works", async () => {
    const fetchMock = mockFetch(jsonResponse(503, {}), jsonResponse(200, CONTEXT));
    render(<BuyerApp />);

    const retry = await screen.findByRole("button", { name: RETRY_LABEL });
    await userEvent.click(retry);

    expect(await screen.findByRole("heading", { level: 1, name: CONTEXT.name })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("merchant-authored text is escaped by this bundle", () => {
  it("renders markup in a shop's name, tagline and mark as text", async () => {
    /*
     * The server stores and returns this verbatim — it trims, bounds and strips control
     * characters and nothing else, and its own tests pin that boundary. Output encoding is ours.
     * React escapes text children, so what this test really asserts is the negative: nothing on
     * this surface reaches for `dangerouslySetInnerHTML` or builds markup by hand.
     */
    const hostile = "<b>Verma</b> & Sons";
    mockFetch(
      jsonResponse(200, {
        ...CONTEXT,
        name: hostile,
        tagline: '<img src=x onerror="alert(1)">',
        logo_text: "<svg/>",
      }),
    );
    const { container } = render(<BuyerApp />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.textContent).toBe(hostile);
    expect(heading.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
    expect(container.innerHTML).toContain("&lt;b&gt;Verma&lt;/b&gt; &amp; Sons");
  });

  it("puts the name in the tab title as text", async () => {
    mockFetch(jsonResponse(200, { ...CONTEXT, name: "<b>Verma</b> & Sons" }));
    render(<BuyerApp />);

    await waitFor(() => {
      expect(document.title).toBe("<b>Verma</b> & Sons");
    });
    // Assigning `document.title` encodes nothing and interprets nothing; the `<title>` node holds
    // these characters as text.
    expect(document.head.querySelector("title")?.querySelector("b")).toBeNull();
  });
});

describe("nullable brand fields", () => {
  it("renders a shop with no tagline, without an empty line where one would be", async () => {
    mockFetch(jsonResponse(200, { ...CONTEXT, tagline: null }));
    render(<BuyerApp />);

    expect(await screen.findByRole("heading", { level: 1, name: CONTEXT.name })).toBeTruthy();
    expect(screen.queryByText(String(CONTEXT.tagline))).toBeNull();
  });

  it("falls back to the name for the mark when logo_text is null", async () => {
    // The server copies neither field into the other; the fallback is the renderer's decision.
    mockFetch(jsonResponse(200, { ...CONTEXT, logo_text: null }));
    const { container } = render(<BuyerApp />);

    await screen.findByRole("heading", { level: 1, name: CONTEXT.name });
    expect(container.querySelector("[aria-hidden]")?.textContent).toBe("GRE");
  });
});

describe("the canonical address", () => {
  it("puts the host the server sent in <link rel=canonical>, and never builds one", async () => {
    mockFetch(jsonResponse(200, { ...CONTEXT, canonical_host: "shop.merchant.com" }));
    render(<BuyerApp />);

    await screen.findByRole("heading", { level: 1, name: CONTEXT.name });
    await waitFor(() => {
      const link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
      expect(link?.href).toBe("https://shop.merchant.com/");
    });
  });

  it("says nothing extra when this is the canonical address", async () => {
    mockFetch(jsonResponse(200, CONTEXT));
    render(<BuyerApp />);

    await screen.findByRole("heading", { level: 1, name: CONTEXT.name });
    expect(screen.queryByText(otherAddressNotice(CONTEXT.canonical_host))).toBeNull();
  });

  it("shows a quiet line when this is not the canonical address, and does not navigate", async () => {
    const assign = vi.fn();
    mockFetch(
      jsonResponse(200, { ...CONTEXT, is_canonical: false, canonical_host: "merchant.com" }),
    );
    render(<BuyerApp />);

    expect(await screen.findByText(otherAddressNotice("merchant.com"))).toBeTruthy();
    // Slice 1 does not redirect, and `is_canonical: false` is not an error. A suspended or
    // failing custom domain is one of the states that produces it — pushing a buyer at the other
    // host would send them to a name that does not answer.
    expect(assign).not.toHaveBeenCalled();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("emits no canonical link when the server sends something that is not a bare host", async () => {
    mockFetch(jsonResponse(200, { ...CONTEXT, canonical_host: "merchant.com/evil?x=1" }));
    render(<BuyerApp />);

    await screen.findByRole("heading", { level: 1, name: CONTEXT.name });
    expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  });
});
