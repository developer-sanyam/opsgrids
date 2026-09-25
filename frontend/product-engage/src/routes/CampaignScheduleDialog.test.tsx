import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";
import { wallClockToInstant } from "@opsgrids/ui";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, CAMPAIGN_SCHEDULED } from "@/api/fixtures";
import type { CampaignOut } from "@/api/types";
import { CampaignScheduleDialog } from "./CampaignScheduleDialog";

/**
 * Scheduling and rescheduling: the one endpoint both call, the future-only rule checked here
 * before the service ever sees the request, a confirmation step between the picker and the
 * write, and a refusal that lands back on the picker with the chosen time still in it.
 */

const IST = "Asia/Kolkata";
// A fixed instant far enough out that "must be in the future" never becomes flaky.
const FUTURE_WALL_CLOCK = "2099-01-15T18:30";
const FUTURE_INSTANT = wallClockToInstant(FUTURE_WALL_CLOCK, IST);
const PAST_WALL_CLOCK = "2020-01-15T18:30";

function mount(campaign: CampaignOut, onScheduled = vi.fn(), onClose = vi.fn()) {
  const user = userEvent.setup();
  render(
    <CampaignScheduleDialog
      open
      campaign={campaign}
      onClose={onClose}
      onScheduled={onScheduled}
    />,
  );
  return { user, onScheduled, onClose };
}

/** This sandbox already runs on IST, so the field's own zone select offers exactly the one
 *  option — asserted below rather than assumed, so a change to that logic is caught here too. */
function timeZoneSelect(): HTMLSelectElement {
  return screen.getByLabelText("Time zone") as HTMLSelectElement;
}

// `datetime-local` is a segmented native control `userEvent.type` cannot drive reliably in
// jsdom — the same reason `DateTimeField`'s own tests (`opsgrids-ui`) set it with `fireEvent`.
function setTime(wallClock: string) {
  fireEvent.change(screen.getByLabelText("Send at"), { target: { value: wallClock } });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("scheduling a draft", () => {
  it("offers the browser's own zone by default, with nothing to pick when it is already IST", () => {
    // Whichever name this runtime's ICU data resolves the system zone to ("Asia/Kolkata" and the
    // older alias "Asia/Calcutta" are the same zone, and different builds report either one).
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    mount(CAMPAIGN_DRAFT);
    const select = timeZoneSelect();
    expect(Array.from(select.options).map((o) => o.value)).toEqual([detected]);
    expect(select.value).toBe(detected);
  });

  it("refuses to continue with no time chosen", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Choose when this campaign should send.")).toBeTruthy();
    // Still on the picker — no confirmation heading, no "Schedule campaign" button yet.
    expect(screen.queryByRole("button", { name: "Schedule campaign" })).toBeNull();
  });

  it("refuses a time that is not in the future, client-side, before any request", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    const spy = vi.spyOn(client, "scheduleCampaign");
    setTime(PAST_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByText("Choose a time in the future.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("moves to a confirmation step naming the exact instant before calling the service", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    const spy = vi.spyOn(client, "scheduleCampaign");
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByText(/Schedule “October delivery network update” to send at/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Schedule campaign" })).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("schedules on confirm and announces the campaign's own name and time", async () => {
    const { user, onScheduled } = mount(CAMPAIGN_DRAFT);
    const scheduled: CampaignOut = { ...CAMPAIGN_DRAFT, status: "scheduled", scheduled_at: FUTURE_INSTANT };
    const spy = vi.spyOn(client, "scheduleCampaign").mockResolvedValue(scheduled);
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Schedule campaign" }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(CAMPAIGN_DRAFT.id, { scheduled_at: FUTURE_INSTANT }),
    );
    expect(onScheduled).toHaveBeenCalledWith(
      scheduled,
      expect.stringContaining("“October delivery network update” is scheduled to send"),
    );
  });

  it("can step back from the confirmation without losing the chosen time", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Back" }));

    expect((screen.getByLabelText("Send at") as HTMLInputElement).value).toBe(FUTURE_WALL_CLOCK);
  });

  it("keeps the chosen time and shows the service's own sentence, verbatim, on a refusal", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    vi.spyOn(client, "scheduleCampaign").mockRejectedValue(
      new ApiError(422, "Add a subject before sending.", null),
    );
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Schedule campaign" }));

    const alert = await screen.findByText("Add a subject before sending.");
    expect(within(alert.closest('[role="alert"]') as HTMLElement).getByText("This wasn't scheduled")).toBeTruthy();
    // Back on the picker, with what was chosen still there.
    expect((screen.getByLabelText("Send at") as HTMLInputElement).value).toBe(FUTURE_WALL_CLOCK);
  });

  it("shows a 409 verbatim too, and does not retry it", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    const spy = vi.spyOn(client, "scheduleCampaign").mockRejectedValue(
      new ApiError(409, "Only a draft or scheduled campaign can be scheduled; this campaign is sending", null),
    );
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Schedule campaign" }));

    expect(
      await screen.findByText(
        "Only a draft or scheduled campaign can be scheduled; this campaign is sending",
      ),
    ).toBeTruthy();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("disables the confirm button while the request is in flight, so a double click cannot double-schedule", async () => {
    const { user } = mount(CAMPAIGN_DRAFT);
    let release: (value: CampaignOut) => void = () => {};
    const spy = vi.spyOn(client, "scheduleCampaign").mockReturnValue(
      new Promise<CampaignOut>((resolve) => {
        release = resolve;
      }),
    );
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const confirmButton = screen.getByRole("button", { name: "Schedule campaign" });
    await user.click(confirmButton);

    expect(screen.getByRole("button", { name: "Scheduling…" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Scheduling…" }));
    expect(spy).toHaveBeenCalledTimes(1);
    release({ ...CAMPAIGN_DRAFT, status: "scheduled", scheduled_at: FUTURE_INSTANT });
  });
});

describe("rescheduling", () => {
  it("opens on the campaign's current schedule, in India Standard Time", () => {
    mount(CAMPAIGN_SCHEDULED);
    expect(screen.getByRole("heading", { name: "Reschedule this campaign" })).toBeTruthy();
    const field = screen.getByLabelText("Send at") as HTMLInputElement;
    // "2026-10-15T12:30:00Z" is 18:00 IST.
    expect(field.value).toBe("2026-10-15T18:00");
  });

  it("says a reschedule, not a schedule, at every step", async () => {
    const { user, onScheduled } = mount(CAMPAIGN_SCHEDULED);
    const rescheduled: CampaignOut = { ...CAMPAIGN_SCHEDULED, scheduled_at: FUTURE_INSTANT };
    vi.spyOn(client, "scheduleCampaign").mockResolvedValue(rescheduled);
    setTime(FUTURE_WALL_CLOCK);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText(/Move “Diwali sale announcement” to send at/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Reschedule campaign" }));
    await waitFor(() =>
      expect(onScheduled).toHaveBeenCalledWith(
        rescheduled,
        expect.stringContaining("“Diwali sale announcement” is rescheduled for"),
      ),
    );
  });
});
