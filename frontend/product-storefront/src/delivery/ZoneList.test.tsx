import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FIXTURE_STORE, FIXTURE_ZONES } from "./fixtures";
import type { ZoneOut } from "./types";

const deleteZone = vi.fn<(id: string) => Promise<void>>();
vi.mock("./api", () => ({ deleteZone: (id: string) => deleteZone(id) }));

import { ZoneList } from "./ZoneList";

const EMPTY_ZONE: ZoneOut | undefined = FIXTURE_ZONES.find((zone) => (zone.postal_code_count ?? 0) === 0);
const ZONE: ZoneOut = EMPTY_ZONE ?? { ...(FIXTURE_ZONES[0] as ZoneOut), postal_code_count: 0 };

function renderList(canWrite: boolean, onChanged = vi.fn()) {
  const onEdit = vi.fn();
  const onPostalCodes = vi.fn();
  render(
    <ZoneList
      zones={[ZONE]}
      store={FIXTURE_STORE}
      canWrite={canWrite}
      onEdit={onEdit}
      onPostalCodes={onPostalCodes}
      onCreate={vi.fn()}
      onChanged={onChanged}
    />,
  );
  return { onEdit, onPostalCodes, onChanged };
}

/** The zone's row controls; a table and a card list are both in the tree under jsdom. */
function firstOf(name: string): HTMLElement {
  const found = screen.getAllByRole("button", { name });
  if (!found[0]) throw new Error(`no ${name}`);
  return found[0];
}

beforeEach(() => {
  deleteZone.mockReset();
  deleteZone.mockResolvedValue(undefined);
});

describe("a zone's controls", () => {
  it("keeps Postal codes in the row and puts Edit and Delete behind one menu", () => {
    renderList(true);
    expect(screen.queryByRole("button", { name: `Delete ${ZONE.name}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `Edit ${ZONE.name}` })).toBeNull();
    expect(firstOf(`Postal codes ${ZONE.name}`)).toBeTruthy();

    fireEvent.click(firstOf(`More actions for ${ZONE.name}`));
    const menu = screen.getAllByRole("menu")[0] as HTMLElement;
    expect(within(menu).getByRole("menuitem", { name: "Edit" })).toBeTruthy();
    const del = within(menu).getByRole("menuitem", { name: "Delete" });
    expect(del.className).toContain("text-negative");
  });

  it("asks before deleting, and deletes only once it is confirmed", async () => {
    const { onChanged } = renderList(true);
    fireEvent.click(firstOf(`More actions for ${ZONE.name}`));
    fireEvent.click(within(screen.getAllByRole("menu")[0] as HTMLElement).getByRole("menuitem", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    expect(deleteZone).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete zone" }));
    await waitFor(() => expect(deleteZone).toHaveBeenCalledWith(ZONE.id));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(`${ZONE.name} deleted.`));
  });

  it("offers a read-only viewer Postal codes and nothing that writes", () => {
    renderList(false);
    expect(firstOf(`Postal codes ${ZONE.name}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: `More actions for ${ZONE.name}` })).toBeNull();
  });
});
