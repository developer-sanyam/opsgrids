import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Page, WhatsAppConnectionOut } from "@/api/types";
import { WhatsAppConnectionPage } from "./WhatsAppConnection";

const TOKEN = "EAAG-super-secret-access-token-9182"; // pragma: allowlist secret

const CONNECTED: WhatsAppConnectionOut = {
  waba_id: "102938475610234",
  phone_number_id: "109827364501928",
  display_number: "+91 98765 43210",
  status: "connected",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <WhatsAppConnectionPage />
    </MemoryRouter>,
  );
}

describe("loading", () => {
  it("shows a skeleton while the read is in flight", () => {
    vi.spyOn(client, "getWhatsAppConnection").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"], [aria-hidden]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("shows the connect form, not a placeholder, when nothing is connected yet", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByRole("button", { name: "Connect account" })).toBeTruthy();
    expect(screen.getByLabelText("WhatsApp Business Account ID")).toBeTruthy();
  });
});

describe("error", () => {
  it("shows a real failure for anything other than a 402", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockRejectedValue(
      new ApiError(500, "Internal server error", null),
    );
    renderPage();
    expect(await screen.findByText("Couldn't load the WhatsApp connection")).toBeTruthy();
  });
});

describe("402 — engage not active", () => {
  it("offers the dashboard's Products page rather than a raw error", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockRejectedValue(
      new ApiError(402, "Engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open Products/ })).toBeTruthy();
  });
});

describe("populated", () => {
  it("shows the connected account without ever rendering the token", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([CONNECTED]));
    renderPage();
    expect(await screen.findByText("+91 98765 43210")).toBeTruthy();
    expect(screen.getByText("102938475610234")).toBeTruthy();
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(document.body.textContent).not.toContain(TOKEN);
  });
});

describe("connecting", () => {
  it("submits the typed fields and never asks the token back", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([]));
    const spy = vi.spyOn(client, "connectWhatsApp").mockResolvedValue(CONNECTED);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("button", { name: "Connect account" });
    await user.type(screen.getByLabelText("WhatsApp Business Account ID"), CONNECTED.waba_id);
    await user.type(screen.getByLabelText("Phone number ID"), CONNECTED.phone_number_id);
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Connect account" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[0]).toEqual({
      waba_id: CONNECTED.waba_id,
      phone_number_id: CONNECTED.phone_number_id,
      access_token: TOKEN,
    });
    expect(await screen.findByText("Connected")).toBeTruthy();
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("keeps the typed fields and shows the server's own message when the write fails", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([]));
    vi.spyOn(client, "connectWhatsApp").mockRejectedValue(
      new ApiError(409, "This organization already has a WhatsApp account connected.", null),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("button", { name: "Connect account" });
    await user.type(screen.getByLabelText("WhatsApp Business Account ID"), "waba-1");
    await user.type(screen.getByLabelText("Phone number ID"), "phone-1");
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Connect account" }));

    expect(await screen.findByText(/already has a WhatsApp account connected/)).toBeTruthy();
    expect((screen.getByLabelText("WhatsApp Business Account ID") as HTMLInputElement).value).toBe(
      "waba-1",
    );
  });

  it("requires the fields the service requires before sending anything", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([]));
    const spy = vi.spyOn(client, "connectWhatsApp");
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Connect account" }));

    expect(screen.getByText(/The WhatsApp Business Account \(WABA\) ID/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("personal whatsapp qr", () => {
  it("renders personal whatsapp qr instructions and ai simulator", async () => {
    vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Personal WhatsApp (QR Code Multi-Device)")).toBeTruthy();
    expect(screen.getByText("Bridge Ready")).toBeTruthy();
    expect(screen.getByText(/uv run python -m tools.whatsapp_personal.cli connect/)).toBeTruthy();

    const simBtn = screen.getByRole("button", { name: "Simulate Auto-Reply" });
    expect(simBtn).toBeTruthy();
    await user.click(simBtn);

    expect(await screen.findByText(/AI Response \(WhatsApp channel\):/)).toBeTruthy();
  });
});
