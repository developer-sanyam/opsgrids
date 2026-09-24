/**
 * NEW-5 — the "Linked member" field on the employee page (ADR-0008 Q1).
 *
 * The properties worth pinning are the negative ones: the field does not exist for someone without
 * `core.identity.membership.write` at ORG (and makes no request on their behalf); no member's email
 * is ever on screen unmasked; a link that the server refuses leaves the screen where it was, with the
 * server's own sentence; and unlinking says what it costs before it asks, and never fires on a Cancel.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { EmployeeOut, MemberOut } from "@/api/types";
import { LinkedMemberSection } from "./LinkedMemberSection";

const getMemberLinkedTo = vi.fn();
const listUnlinkedMembers = vi.fn();
const linkMember = vi.fn();
const unlinkMember = vi.fn();

vi.mock("@/api/members", () => ({
  MEMBER_PICKER_SIZE: 20,
  getMemberLinkedTo: (...args: unknown[]) => getMemberLinkedTo(...args),
  listUnlinkedMembers: (...args: unknown[]) => listUnlinkedMembers(...args),
  linkMember: (...args: unknown[]) => linkMember(...args),
  unlinkMember: (...args: unknown[]) => unlinkMember(...args),
}));

const WRITE_ORG = ["core.identity.membership.write:ORG"];

const EMPLOYEE = {
  id: "emp-ananya",
  full_name: "Ananya Iyer",
} as EmployeeOut;

function member(overrides: Partial<MemberOut> = {}): MemberOut {
  return {
    id: "mem-asha",
    user_id: "user-asha",
    email: "asha.verma@acme.example",
    full_name: "Asha Verma",
    display_name: null,
    job_title: null,
    status: "active",
    is_owner: false,
    org_unit_id: null,
    manager_membership_id: null,
    location_codes: [],
    employee_id: null,
    roles: [],
    joined_at: null,
    started_on: null,
    ...overrides,
  };
}

const LINKED = member({ employee_id: "emp-ananya" });

function page(items: MemberOut[]) {
  return { items, total: items.length, page: 1, size: 20, pages: 1 };
}

function renderField(grants: readonly string[] = WRITE_ORG) {
  return render(
    <PermissionProvider grants={grants}>
      <LinkedMemberSection employee={EMPLOYEE} />
    </PermissionProvider>,
  );
}

async function pickMember(query: string, name: RegExp) {
  const input = screen.getByRole("combobox", { name: "Member to link" });
  await userEvent.type(input, query);
  await userEvent.click(await screen.findByRole("option", { name }));
}

describe("LinkedMemberSection", () => {
  beforeEach(() => {
    getMemberLinkedTo.mockReset();
    listUnlinkedMembers.mockReset();
    linkMember.mockReset();
    unlinkMember.mockReset();
    getMemberLinkedTo.mockResolvedValue(null);
    listUnlinkedMembers.mockResolvedValue(
      page([member({ id: "mem-asha" }), member({ id: "mem-asha-2", email: "asha.rao@other.example", full_name: "Asha Rao" })]),
    );
  });

  describe("who sees it", () => {
    it("renders nothing, and asks for nothing, without the permission", () => {
      const { container } = renderField([]);
      expect(container.firstChild).toBeNull();
      expect(getMemberLinkedTo).not.toHaveBeenCalled();
    });

    it("renders nothing when the grant is held at a scope below ORG", () => {
      const { container } = renderField(["core.identity.membership.write:OWN"]);
      expect(container.firstChild).toBeNull();
      expect(getMemberLinkedTo).not.toHaveBeenCalled();
    });

    it("renders nothing for a caller who can only read members", () => {
      const { container } = renderField(["core.identity.membership.read:ORG"]);
      expect(container.firstChild).toBeNull();
      expect(getMemberLinkedTo).not.toHaveBeenCalled();
    });
  });

  describe("loading and failing", () => {
    it("shows a busy placeholder while the link is being read", () => {
      getMemberLinkedTo.mockReturnValue(new Promise(() => undefined));
      renderField();
      expect(screen.getByLabelText("Loading the linked member").getAttribute("aria-busy")).toBe("true");
      expect(screen.queryByRole("combobox")).toBeNull();
    });

    it("says what failed and retries on request", async () => {
      getMemberLinkedTo.mockRejectedValueOnce(new ApiError(500, "boom", null));
      renderField();

      expect(await screen.findByText("Couldn't load the linked member.")).toBeDefined();
      getMemberLinkedTo.mockResolvedValueOnce(LINKED);
      await userEvent.click(screen.getByRole("button", { name: /try again/i }));

      expect(await screen.findByText("Asha Verma")).toBeDefined();
      expect(getMemberLinkedTo).toHaveBeenCalledTimes(2);
    });

    it("explains a 403 on the directory read instead of pretending nobody is linked", async () => {
      getMemberLinkedTo.mockRejectedValue(new ApiError(403, "Missing permission", null));
      renderField();
      expect(
        await screen.findByText(
          "You don't have access to the member directory, so the linked member can't be shown.",
        ),
      ).toBeDefined();
      expect(screen.queryByRole("combobox")).toBeNull();
    });
  });

  describe("linked", () => {
    beforeEach(() => {
      getMemberLinkedTo.mockResolvedValue(LINKED);
    });

    it("asks which member is linked to this very employee", async () => {
      renderField();
      await screen.findByText("Asha Verma");
      expect(getMemberLinkedTo).toHaveBeenCalledWith("emp-ananya");
    });

    it("shows the member with the email masked — never whole", async () => {
      renderField();
      await screen.findByText("Asha Verma");

      expect(screen.getByText("a•••@a•••")).toBeDefined();
      expect(document.body.textContent).not.toContain("asha.verma@acme.example");
      expect(document.body.textContent).not.toContain("acme");
    });

    it("says so when the member has not finished joining", async () => {
      getMemberLinkedTo.mockResolvedValue(member({ employee_id: "emp-ananya", status: "invited" }));
      renderField();
      expect(await screen.findByText("invited")).toBeDefined();
    });

    it("prefers a display name to the legal one", async () => {
      getMemberLinkedTo.mockResolvedValue(member({ display_name: "Ash", employee_id: "emp-ananya" }));
      renderField();
      expect(await screen.findByText("Ash")).toBeDefined();
    });

    it("offers no picker while a member is linked", async () => {
      renderField();
      await screen.findByText("Asha Verma");
      expect(screen.queryByRole("combobox")).toBeNull();
    });
  });

  describe("unlinked", () => {
    it("says what being unlinked costs the person and offers the picker", async () => {
      renderField();
      expect(await screen.findByText(/No member is linked to Ananya Iyer/)).toBeDefined();
      expect(screen.getByText(/approvals inbox stays empty/)).toBeDefined();
      expect(screen.getByRole("combobox", { name: "Member to link" })).toBeDefined();
    });

    it("keeps Link member disabled until somebody is picked", async () => {
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      expect((screen.getByRole("button", { name: "Link member" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("lists only unlinked members, searched by what was typed", async () => {
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });

      await userEvent.type(screen.getByRole("combobox", { name: "Member to link" }), "as");
      await screen.findByRole("option", { name: /Asha Verma/ });

      expect(listUnlinkedMembers).toHaveBeenCalledWith(expect.objectContaining({ q: "as" }));
    });

    it("shows every candidate with a masked email, never a whole one", async () => {
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      await userEvent.type(screen.getByRole("combobox", { name: "Member to link" }), "as");
      await screen.findByRole("option", { name: /Asha Verma/ });

      const listbox = screen.getByRole("listbox");
      expect(within(listbox).getByText("a•••@a•••")).toBeDefined();
      expect(within(listbox).getByText("a•••@o•••")).toBeDefined();
      expect(document.body.textContent).not.toContain("asha.verma@acme.example");
      expect(document.body.textContent).not.toContain("asha.rao@other.example");
    });

    it("links the picked member to this employee and shows the result", async () => {
      linkMember.mockResolvedValue(member({ employee_id: "emp-ananya" }));
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });

      await pickMember("as", /Asha Verma/);
      await userEvent.click(screen.getByRole("button", { name: "Link member" }));

      await waitFor(() => expect(linkMember).toHaveBeenCalledWith("mem-asha", "emp-ananya"));
      expect(await screen.findByRole("button", { name: "Unlink" })).toBeDefined();
      expect(screen.getByText("a•••@a•••")).toBeDefined();
      expect(screen.queryByRole("combobox")).toBeNull();
    });

    it("sends the link once, however many times the button is pressed", async () => {
      let resolve: (m: MemberOut) => void = () => undefined;
      linkMember.mockReturnValue(new Promise<MemberOut>((r) => (resolve = r)));
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      await pickMember("as", /Asha Verma/);

      const button = screen.getByRole("button", { name: "Link member" });
      await userEvent.dblClick(button);
      resolve(member({ employee_id: "emp-ananya" }));

      await screen.findByRole("button", { name: "Unlink" });
      expect(linkMember).toHaveBeenCalledTimes(1);
    });

    it("shows the server's own sentence on a 409 and stays where it was", async () => {
      linkMember.mockRejectedValue(
        new ApiError(409, "conflict", {
          detail: "That employee is already linked to another member of this organization",
        }),
      );
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      await pickMember("as", /Asha Verma/);

      await userEvent.click(screen.getByRole("button", { name: "Link member" }));

      expect(
        await screen.findByText("That employee is already linked to another member of this organization"),
      ).toBeDefined();
      expect(screen.queryByRole("button", { name: "Unlink" })).toBeNull();
      // The selection survives so the person can try again or choose someone else.
      expect(screen.getByLabelText(/Remove Asha Verma/)).toBeDefined();
      expect((screen.getByRole("button", { name: "Link member" }) as HTMLButtonElement).disabled).toBe(false);
    });

    it("rewrites a 403 into a plain sentence rather than a permission code", async () => {
      linkMember.mockRejectedValue(new ApiError(403, "Missing permission: core.identity.membership.write", null));
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      await pickMember("as", /Asha Verma/);

      await userEvent.click(screen.getByRole("button", { name: "Link member" }));

      expect(await screen.findByText("You don't have permission to link members.")).toBeDefined();
      expect(document.body.textContent).not.toContain("core.identity");
    });

    it("clears a failure once a different member is chosen", async () => {
      linkMember.mockRejectedValue(new ApiError(404, "gone", { detail: "That person is not a member of this organization" }));
      renderField();
      await screen.findByRole("combobox", { name: "Member to link" });
      await pickMember("as", /Asha Verma/);
      await userEvent.click(screen.getByRole("button", { name: "Link member" }));
      await screen.findByText("That person is not a member of this organization");

      await userEvent.click(screen.getByLabelText(/Remove Asha Verma/));

      expect(screen.queryByText("That person is not a member of this organization")).toBeNull();
    });
  });

  describe("unlinking", () => {
    beforeEach(() => {
      getMemberLinkedTo.mockResolvedValue(LINKED);
    });

    async function openConfirmation() {
      renderField();
      await userEvent.click(await screen.findByRole("button", { name: "Unlink" }));
      return screen.findByRole("dialog");
    }

    it("says what it costs before it asks — including the tenant-wide refresh", async () => {
      const dialog = await openConfirmation();

      expect(within(dialog).getByText("Unlink Asha Verma?")).toBeDefined();
      expect(within(dialog).getByText(/refreshes everyone's sign-in in the organization/)).toBeDefined();
      expect(within(dialog).getByText(/will stop acting as Ananya Iyer/)).toBeDefined();
      expect(unlinkMember).not.toHaveBeenCalled();
    });

    it("does nothing on Cancel", async () => {
      const dialog = await openConfirmation();
      await userEvent.click(within(dialog).getByRole("button", { name: "Keep the link" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(unlinkMember).not.toHaveBeenCalled();
      expect(screen.getByText("Asha Verma")).toBeDefined();
    });

    it("does nothing on Escape", async () => {
      await openConfirmation();
      await userEvent.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(unlinkMember).not.toHaveBeenCalled();
    });

    it("puts focus on the safe choice, not the destructive one", async () => {
      const dialog = await openConfirmation();
      expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Keep the link" }));
    });

    it("unlinks the linked member and returns to the picker", async () => {
      unlinkMember.mockResolvedValue(member({ employee_id: null }));
      const dialog = await openConfirmation();

      await userEvent.click(within(dialog).getByRole("button", { name: "Unlink" }));

      await waitFor(() => expect(unlinkMember).toHaveBeenCalledWith("mem-asha"));
      expect(await screen.findByRole("combobox", { name: "Member to link" })).toBeDefined();
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("keeps the dialog open with the server's sentence when it fails", async () => {
      unlinkMember.mockRejectedValue(new ApiError(500, "boom", { detail: "The directory is unavailable" }));
      const dialog = await openConfirmation();

      await userEvent.click(within(dialog).getByRole("button", { name: "Unlink" }));

      expect(await screen.findByText("The directory is unavailable")).toBeDefined();
      expect(screen.getByRole("dialog")).toBeDefined();
      expect(screen.getByText("Asha Verma")).toBeDefined();
    });

    it("rewrites a 403 into a plain sentence", async () => {
      unlinkMember.mockRejectedValue(new ApiError(403, "Missing permission: core.identity.membership.write", null));
      const dialog = await openConfirmation();

      await userEvent.click(within(dialog).getByRole("button", { name: "Unlink" }));

      expect(await screen.findByText("You don't have permission to unlink members.")).toBeDefined();
    });

    it("treats a 404 as already unlinked — another tab got there first", async () => {
      unlinkMember.mockRejectedValue(new ApiError(404, "gone", null));
      const dialog = await openConfirmation();

      await userEvent.click(within(dialog).getByRole("button", { name: "Unlink" }));

      expect(await screen.findByRole("combobox", { name: "Member to link" })).toBeDefined();
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("unlinks once however many times Unlink is pressed", async () => {
      let resolve: (m: MemberOut) => void = () => undefined;
      unlinkMember.mockReturnValue(new Promise<MemberOut>((r) => (resolve = r)));
      const dialog = await openConfirmation();

      await userEvent.dblClick(within(dialog).getByRole("button", { name: "Unlink" }));
      resolve(member({ employee_id: null }));

      await screen.findByRole("combobox", { name: "Member to link" });
      expect(unlinkMember).toHaveBeenCalledTimes(1);
    });
  });
});
