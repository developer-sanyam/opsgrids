import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DetailList, PropertyRow } from "./DetailList";

describe("DetailList", () => {
  it("is a description list: one term and one description per item, with its hint", () => {
    const { container } = render(
      <DetailList
        items={[
          { label: "Owner", value: "Asha Menon" },
          { label: "GSTIN", value: "29ABCDE1234F1Z5", hint: "Verified 2 Oct" },
        ]}
      />,
    );
    const list = container.querySelector("dl");
    if (list === null) throw new Error("no <dl>");
    expect(Array.from(list.querySelectorAll("dt")).map((term) => term.textContent)).toEqual(["Owner", "GSTIN"]);
    expect(list.querySelectorAll("dd")).toHaveLength(2);
    expect(within(list).getByText("Verified 2 Oct")).toBeTruthy();
  });

  it("lays out in two columns when asked", () => {
    const { container } = render(<DetailList columns={2} items={[{ label: "City", value: "Pune" }]} />);
    expect(container.querySelector("dl")?.className).toContain("sm:grid-cols-2");
  });
});

describe("PropertyRow", () => {
  it("has no edit control without onEdit", () => {
    render(
      <dl>
        <PropertyRow label="Owner">Asha Menon</PropertyRow>
      </dl>,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Owner").tagName).toBe("DT");
  });

  it("names its edit button after the row, or after editLabel", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const { rerender } = render(
      <dl>
        <PropertyRow label="Owner" onEdit={onEdit}>
          Asha Menon
        </PropertyRow>
      </dl>,
    );
    await user.click(screen.getByRole("button", { name: "Edit Owner" }));
    expect(onEdit).toHaveBeenCalledTimes(1);

    rerender(
      <dl>
        <PropertyRow label="Owner" onEdit={onEdit} editLabel="Change the deal owner">
          Asha Menon
        </PropertyRow>
      </dl>,
    );
    expect(screen.getByRole("button", { name: "Change the deal owner" })).toBeTruthy();
  });
});
