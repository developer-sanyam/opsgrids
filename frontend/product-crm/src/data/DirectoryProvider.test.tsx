import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DirectoryProvider } from "./DirectoryProvider";
import { useDirectory } from "./directoryContext";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const OWNER = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE" };
const COMPANY_PAGE = { items: [{ id: "comp-1", name: "Northwind" }], total: 1, page: 1, size: 200, pages: 1 };
const CONTACT_PAGE = { items: [{ id: "cont-1", full_name: "Asha Rao" }], total: 1, page: 1, size: 200, pages: 1 };

afterEach(() => {
  vi.unstubAllGlobals();
});

function Probe() {
  const { owners, companies, contacts, status } = useDirectory();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="owners">{owners.length}</span>
      <span data-testid="companies">{companies.length}</span>
      <span data-testid="contacts">{contacts.length}</span>
    </div>
  );
}

describe("DirectoryProvider", () => {
  it("starts loading, then resolves owners/companies/contacts from the three gateway calls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const url = String(input);
        if (url.includes("/customers/owners")) return Promise.resolve(jsonResponse([OWNER]));
        if (url.includes("/customers")) return Promise.resolve(jsonResponse(COMPANY_PAGE));
        if (url.includes("/contacts")) return Promise.resolve(jsonResponse(CONTACT_PAGE));
        throw new Error(`unexpected request: ${url}`);
      }),
    );

    render(
      <DirectoryProvider>
        <Probe />
      </DirectoryProvider>,
    );

    expect(screen.getByTestId("status").textContent).toBe("loading");

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("success"));
    expect(screen.getByTestId("owners").textContent).toBe("1");
    expect(screen.getByTestId("companies").textContent).toBe("1");
    expect(screen.getByTestId("contacts").textContent).toBe("1");
  });

  it("degrades to an error status rather than throwing when a directory call fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    await act(async () => {
      render(
        <DirectoryProvider>
          <Probe />
        </DirectoryProvider>,
      );
    });

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("error"));
    // Empty, not fixture data — the honest degraded state.
    expect(screen.getByTestId("owners").textContent).toBe("0");
  });

  it("throws a clear error when useDirectory is read outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow("useDirectory must be used within a DirectoryProvider");
    spy.mockRestore();
  });
});
