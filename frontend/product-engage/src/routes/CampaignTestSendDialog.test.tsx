import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { CampaignTestSendDialog } from "./CampaignTestSendDialog";

/**
 * A real send of a campaign's content, to an address of the caller's own choosing, that never
 * touches the real audience — pinned here: the address is required, the result names where it
 * went, a refusal keeps the typed address, and nothing here claims domain verification is needed.
 */

function mount(onSent = vi.fn()) {
  const user = userEvent.setup();
  render(
    <CampaignTestSendDialog
      open
      campaignId="c1a2b3c4-1111-2222-3333-444455556666"
      campaignName="Autumn sale"
      onClose={vi.fn()}
      onSent={onSent}
    />,
  );
  return { user, onSent };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the campaign preview send dialog", () => {
  it("does not submit an empty address", async () => {
    const { user } = mount();
    const send = vi.spyOn(client, "sendCampaignTest");

    await user.click(screen.getByRole("button", { name: "Send test" }));

    expect(await screen.findByText(/Where should the test go/)).toBeTruthy();
    expect(send).not.toHaveBeenCalled();
  });

  it("sends to the typed address and reports where it went", async () => {
    const { user, onSent } = mount();
    vi.spyOn(client, "sendCampaignTest").mockResolvedValue({
      sent: true,
      to: "ops@vaayu-logistics.example.com",
    });

    await user.type(screen.getByLabelText("Send it to"), "ops@vaayu-logistics.example.com");
    await user.click(screen.getByRole("button", { name: "Send test" }));

    await waitFor(() =>
      expect(onSent).toHaveBeenCalledWith(
        "Test of “Autumn sale” sent to ops@vaayu-logistics.example.com.",
      ),
    );
    expect(client.sendCampaignTest).toHaveBeenCalledWith(
      "c1a2b3c4-1111-2222-3333-444455556666",
      { to_address: "ops@vaayu-logistics.example.com" },
    );
  });

  it("keeps the typed address and shows the service's own refusal", async () => {
    const { user } = mount();
    vi.spyOn(client, "sendCampaignTest").mockRejectedValue(
      new ApiError(422, "Connect an email provider before sending a test message.", null),
    );

    await user.type(screen.getByLabelText("Send it to"), "ops@vaayu-logistics.example.com");
    await user.click(screen.getByRole("button", { name: "Send test" }));

    const alert = await screen.findByRole("alert");
    expect(
      within(alert).getByText("Connect an email provider before sending a test message."),
    ).toBeTruthy();
    expect((screen.getByLabelText("Send it to") as HTMLInputElement).value).toBe(
      "ops@vaayu-logistics.example.com",
    );
  });

  it("says plainly that no verified domain is required", () => {
    mount();
    expect(screen.getByText("This does not need a verified domain")).toBeTruthy();
  });
});
