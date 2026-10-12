/**
 * Erasing an enquiry from the inbox: offered only to a holder of the delete permission, behind a
 * confirmation, and gone from the list once confirmed. The server refuses the call regardless.
 */

import { afterEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PermissionProvider } from "@opsgrids/client";

import { resetEnquiryFixtures } from "./api";
import { EnquiriesPage } from "./Enquiries";

function renderPage(grants: string[]) {
  return render(
    <PermissionProvider grants={grants}>
      <EnquiriesPage />
    </PermissionProvider>,
  );
}

afterEach(() => resetEnquiryFixtures());

describe("deleting an enquiry", () => {
  it("asks first, then erases the enquiry from the list", async () => {
    const user = userEvent.setup();
    renderPage(["storefront.sales.enquiry.read:ORG", "storefront.sales.enquiry.delete:ORG"]);

    const row = (await screen.findByText("Rajeev Bhatia")).closest("tr");
    if (!row) throw new Error("the enquiry should render as a table row");
    await user.click(within(row).getByRole("button", { name: /Delete/ }));

    const dialog = await screen.findByRole("dialog", { name: "Delete this enquiry?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("Rajeev Bhatia")).toBeNull());
  });

  it("offers no delete without the permission", async () => {
    renderPage(["storefront.sales.enquiry.read:ORG"]);

    const row = (await screen.findByText("Rajeev Bhatia")).closest("tr");
    if (!row) throw new Error("the enquiry should render as a table row");
    expect(within(row).queryByRole("button", { name: /Delete/ })).toBeNull();
  });
});
