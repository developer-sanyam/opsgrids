import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { SegmentOut } from "@/api/types";
import { SegmentFormDialog } from "./SegmentFormDialog";

const SAVED: SegmentOut = {
  id: "5b0c2d9e-1f3a-4c6b-8d7e-9a1b2c3d4e5f",
  name: "Customers",
  list_id: null,
  created_within_days: null,
  active_within_days: null,
  crm_status: "customer",
  created_at: "2026-10-16T09:00:00Z",
  updated_at: "2026-10-16T09:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("segment form: CRM status", () => {
  it("sends the chosen CRM status with the segment", async () => {
    const create = vi.spyOn(client, "createSegment").mockResolvedValue(SAVED);
    const user = userEvent.setup();
    render(<SegmentFormDialog open lists={[]} onClose={() => {}} onSaved={() => {}} />);

    await user.type(screen.getByLabelText(/Segment name/), "Customers");
    await user.selectOptions(screen.getByLabelText("Only contacts with this CRM status"), "customer");
    await user.click(screen.getByRole("button", { name: "Save segment" }));

    expect(create).toHaveBeenCalledWith(expect.objectContaining({ crm_status: "customer" }));
  });

  it("sends no CRM status when none is chosen", async () => {
    const create = vi.spyOn(client, "createSegment").mockResolvedValue({ ...SAVED, crm_status: null });
    const user = userEvent.setup();
    render(<SegmentFormDialog open lists={[]} onClose={() => {}} onSaved={() => {}} />);

    await user.type(screen.getByLabelText(/Segment name/), "Everyone");
    await user.click(screen.getByRole("button", { name: "Save segment" }));

    expect(create).toHaveBeenCalledWith(expect.objectContaining({ crm_status: null }));
  });
});
