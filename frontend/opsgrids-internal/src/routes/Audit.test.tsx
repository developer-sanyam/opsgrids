import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { AuditPage } from "./Audit";

function renderAudit() {
  return render(
    <MemoryRouter>
      <AuditPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AuditPage", () => {
  it("renders rows from the real AuditOut shape, including a null actor and reason", async () => {
    vi.spyOn(client, "listAudit").mockResolvedValue({
      audit: [
        {
          id: "aud_1",
          organization_id: "org_905ffb",
          actor_id: "3f1a5b2c-0000-4000-8000-000000000002",
          acting_as: "operator:billing_ops",
          action: "suspend",
          entity: "subscription",
          entity_id: "sub_905ffb_1",
          reason: "Non-payment after dunning window closed",
          at: "2026-08-12T10:05:00Z",
        },
        {
          id: "aud_2",
          organization_id: "org_e5b440",
          actor_id: null,
          acting_as: null,
          action: "organization.read",
          entity: "organization",
          entity_id: null,
          reason: null,
          at: "2026-08-20T13:48:00Z",
        },
      ],
    });

    renderAudit();
    await waitFor(() => expect(screen.getAllByText("suspend").length).toBeGreaterThan(0));
    expect(screen.getAllByText("organization.read").length).toBeGreaterThan(0);
    // The null-actor row falls back to "system" rather than rendering nothing.
    expect(screen.getByText("system")).toBeTruthy();
  });

  it("shows the empty state for an empty audit log", async () => {
    vi.spyOn(client, "listAudit").mockResolvedValue({ audit: [] });
    renderAudit();
    await waitFor(() => expect(screen.getByText("No matching audit entries")).toBeTruthy());
  });
});
