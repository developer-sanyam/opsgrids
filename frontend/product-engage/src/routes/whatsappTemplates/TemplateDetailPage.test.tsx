import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as api from "@/api/whatsappTemplates";
import { TemplateDetailPage } from "./TemplateDetailPage";
import { DETAIL, PREVIEW, REJECTED } from "./test-fixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

function renderDetail(id = "order_shipped") {
  return render(
    <MemoryRouter initialEntries={[`/whatsapp/templates/${id}`]}>
      <Routes>
        <Route path="/whatsapp/templates/:id" element={<TemplateDetailPage />} />
        <Route path="/whatsapp/templates" element={<p>List screen</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("loading", () => {
  it("holds the layout with a skeleton and says it is loading", () => {
    vi.spyOn(api, "getTemplate").mockReturnValue(new Promise(() => {}));
    const { container } = renderDetail();
    expect(screen.getByText("Loading template")).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("populated", () => {
  it("shows the template, its status and quality, its message, variables and history", async () => {
    const get = vi.spyOn(api, "getTemplate").mockResolvedValue(DETAIL);
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    renderDetail();

    expect(await screen.findByRole("heading", { level: 1, name: "order_shipped" })).toBeTruthy();
    expect(get).toHaveBeenCalledWith("order_shipped");
    expect(screen.getByText("Approved")).toBeTruthy();
    expect(screen.getByText("High quality")).toBeTruthy();
    expect(screen.getByText("Utility")).toBeTruthy();
    expect(screen.getByText("English (US)")).toBeTruthy();
    expect(screen.getByText("1187364502983471")).toBeTruthy();
    expect(screen.getByText("Hello Rahul, your order #ORD-2938 has shipped.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Variables" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Approval history" })).toBeTruthy();
    expect(screen.getByText("Approved by Meta")).toBeTruthy();
    expect(screen.getByRole("link", { name: "All templates" }).getAttribute("href")).toBe("/whatsapp/templates");
  });

  it("says a template Meta rejected was rejected, and why", async () => {
    vi.spyOn(api, "getTemplate").mockResolvedValue({ ...DETAIL, ...REJECTED, history: [] });
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    renderDetail("cod_confirmation");

    expect(await screen.findByText("Meta rejected this template")).toBeTruthy();
    expect(screen.getByText("Rejected")).toBeTruthy();
    expect(screen.getByText(/asks for payment details/)).toBeTruthy();
  });

  it("does not claim an ID or a submission time it was not given", async () => {
    vi.spyOn(api, "getTemplate").mockResolvedValue({
      ...DETAIL,
      status: "draft",
      meta_template_id: null,
      submitted_at: null,
      history: [],
    });
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    renderDetail();

    expect(await screen.findByText("Not assigned yet")).toBeTruthy();
    expect(screen.getByText("Not submitted")).toBeTruthy();
    expect(screen.getByText("No status changes recorded yet.")).toBeTruthy();
  });

  it("says when a template has no variables", async () => {
    vi.spyOn(api, "getTemplate").mockResolvedValue({ ...DETAIL, variables: [] });
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    renderDetail();
    expect(await screen.findByText("This template has no variables.")).toBeTruthy();
  });
});

describe("failures", () => {
  it("says the template does not exist on a 404, and goes back to the list", async () => {
    vi.spyOn(api, "getTemplate").mockRejectedValue(new ApiError(404, "Not found", null));
    const user = userEvent.setup();
    renderDetail("nope");

    expect(await screen.findByText("That template doesn't exist")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Back to templates" }));
    expect(await screen.findByText("List screen")).toBeTruthy();
  });

  it("offers a retry for any other failure", async () => {
    const get = vi
      .spyOn(api, "getTemplate")
      .mockRejectedValueOnce(new ApiError(500, "Internal server error", null))
      .mockResolvedValue(DETAIL);
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText("Couldn't load this template")).toBeTruthy();
    expect(screen.getByText("Internal server error")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "order_shipped" })).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("shows the Engage not-active state on a 402", async () => {
    vi.spyOn(api, "getTemplate").mockRejectedValue(new ApiError(402, "engage is not active", null));
    renderDetail();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});
