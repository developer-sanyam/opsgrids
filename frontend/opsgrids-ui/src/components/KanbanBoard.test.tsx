/**
 * The board's promises: every move is a real menu item a keyboard or a thumb can reach (drag is
 * only for a pointer), a refused move says why instead of vanishing, a card whose column the
 * board does not know is shown rather than thrown on, and a move is said once and keeps focus on
 * the card that moved.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Announcer } from "./Announcer";
import { KanbanBoard } from "./KanbanBoard";
import type { KanbanBoardProps, KanbanColumn } from "./KanbanBoard";

interface Card {
  id: string;
  title: string;
  stage: string;
}

const COLUMNS: KanbanColumn[] = [
  { id: "todo", label: "To do", tone: "neutral" },
  { id: "doing", label: "Doing", tone: "info" },
  { id: "done", label: "Done", tone: "success", closed: true },
];

const CARDS: Card[] = [
  { id: "c1", title: "Acme renewal", stage: "todo" },
  { id: "c2", title: "Globex pilot", stage: "doing" },
];

type Props = Partial<KanbanBoardProps<Card>> & { items?: readonly Card[] };

/** A consumer that applies the move to its own items, the way every app does. */
function Board(props: Props) {
  const [items, setItems] = useState<readonly Card[]>(props.items ?? CARDS);
  return (
    <Announcer>
      <KanbanBoard<Card>
        columns={COLUMNS}
        getItemId={(card) => card.id}
        getColumnId={(card) => card.stage}
        renderCard={(card, state) => (
          <>
            <p>{card.title}</p>
            {state.actions}
          </>
        )}
        canMove={() => true}
        moveMenuLabel={(card) => card.title}
        announce={(card, _from, to, result) => `${card.title} ${result} to ${to}`}
        {...props}
        items={items}
        onMove={async (card, to) => {
          setItems((previous) => previous.map((c) => (c.id === card.id ? { ...c, stage: to } : c)));
          await props.onMove?.(card, to);
        }}
      />
    </Announcer>
  );
}

function column(label: string): HTMLElement {
  return screen.getByRole("region", { name: label });
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, title: string) {
  await user.click(screen.getByRole("button", { name: `More actions for ${title}` }));
  return screen.getByRole("menu", { name: `More actions for ${title}` });
}

describe("KanbanBoard", () => {
  it("every_move_to_item_is_inside_the_menu_and_none_is_an_inline_button", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Board />);
    expect(screen.queryAllByRole("button", { name: /^Move to/ })).toEqual([]);
    const menu = await openMenu(user, "Acme renewal");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Move to Doing",
      "Move to Done",
    ]);
    unmount();

    render(
      <Board
        getExtraActions={() => [
          { label: "Edit", onSelect: vi.fn() },
          { label: "Delete", tone: "danger", onSelect: vi.fn() },
        ]}
      />,
    );
    expect(screen.queryAllByRole("button", { name: /^(Move to|Edit|Delete)/ })).toEqual([]);
    const withExtras = await openMenu(user, "Acme renewal");
    expect(within(withExtras).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Move to Doing",
      "Move to Done",
      "Edit",
      "Delete",
    ]);
  });

  it("a_card_with_one_visible_action_shows_one_inline_button", () => {
    render(<Board columns={COLUMNS.slice(0, 2)} items={[CARDS[0] as Card]} />);
    expect(screen.getByRole("button", { name: "Move to Doing Acme renewal" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /More actions/ })).toBeNull();
  });

  it("moving_through_the_menu_calls_onMove_once", async () => {
    const user = userEvent.setup();
    const onMove = vi.fn(() => Promise.resolve());
    render(<Board onMove={onMove} />);
    const menu = await openMenu(user, "Acme renewal");
    await user.click(within(menu).getByRole("menuitem", { name: "Move to Doing" }));
    expect(onMove).toHaveBeenCalledTimes(1);
    expect(onMove).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }), "doing");
    expect(within(column("Doing")).getByText("Acme renewal")).toBeTruthy();
  });

  it("a_closed_column_refuses_a_drop_and_a_menu_move_is_disabled_with_a_reason", async () => {
    const user = userEvent.setup();
    const onMove = vi.fn(() => Promise.resolve());
    render(<Board onMove={onMove} canMove={(_card, to) => to !== "done"} />);

    const card = screen.getByText("Acme renewal").closest("article");
    if (card === null) throw new Error("no card");
    fireEvent.dragStart(card);
    fireEvent.dragOver(column("Done"));
    fireEvent.drop(column("Done"));
    expect(onMove).not.toHaveBeenCalled();

    const menu = await openMenu(user, "Acme renewal");
    const refused = within(menu).getByRole("menuitem", { name: /Move to Done/ });
    expect(refused.getAttribute("aria-disabled")).toBe("true");
    expect(refused.textContent).toContain("can't move to Done");
    await user.click(refused);
    expect(onMove).not.toHaveBeenCalled();
  });

  it("drops into an open column that accepts the card", () => {
    const onMove = vi.fn(() => Promise.resolve());
    render(<Board onMove={onMove} />);
    const card = screen.getByText("Acme renewal").closest("article");
    if (card === null) throw new Error("no card");
    fireEvent.dragStart(card);
    fireEvent.dragOver(column("Doing"));
    expect(within(column("Doing")).getByText("Drop to move here")).toBeTruthy();
    fireEvent.drop(column("Doing"));
    expect(onMove).toHaveBeenCalledWith(expect.objectContaining({ id: "c1" }), "doing");
  });

  it("announcements_for_moving_moved_failed", async () => {
    const user = userEvent.setup();
    let settle: (ok: boolean) => void = () => undefined;
    const onMove = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          settle = (ok) => (ok ? resolve() : reject(new Error("refused")));
        }),
    );
    render(<Board onMove={onMove} />);

    await user.click(within(await openMenu(user, "Acme renewal")).getByRole("menuitem", { name: "Move to Doing" }));
    expect(screen.getByText("Acme renewal moving to doing")).toBeTruthy();
    expect(screen.getByText("Acme renewal").closest("article")?.getAttribute("aria-busy")).toBe("true");
    await act(async () => settle(true));
    expect(screen.getByText("Acme renewal moved to doing")).toBeTruthy();
    expect(screen.getByText("Acme renewal").closest("article")?.getAttribute("aria-busy")).toBeNull();

    await user.click(within(await openMenu(user, "Acme renewal")).getByRole("menuitem", { name: "Move to To do" }));
    await act(async () => settle(false));
    expect(screen.getByText("Acme renewal failed to todo")).toBeTruthy();
  });

  it("says nothing for a result whose announcement is empty, leaving it to the consumer", async () => {
    const user = userEvent.setup();
    render(<Board announce={(card, _from, _to, result) => (result === "moving" ? `Moving ${card.title}` : "")} />);
    await user.click(within(await openMenu(user, "Acme renewal")).getByRole("menuitem", { name: "Move to Doing" }));
    expect(screen.getByText("Moving Acme renewal")).toBeTruthy();
  });

  it("an_unknown_column_renders_in_Other_and_does_not_throw", async () => {
    const user = userEvent.setup();
    render(<Board items={[...CARDS, { id: "c3", title: "Initech", stage: "archived" }]} />);
    const other = column("Other");
    expect(within(other).getByText("Initech")).toBeTruthy();
    const menu = await openMenu(user, "Initech");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Move to To do",
      "Move to Doing",
      "Move to Done",
    ]);
  });

  it("focus_stays_on_the_moved_card", async () => {
    const user = userEvent.setup();
    render(<Board onMove={() => Promise.resolve()} />);
    await user.click(within(await openMenu(user, "Acme renewal")).getByRole("menuitem", { name: "Move to Doing" }));
    const moved = within(column("Doing")).getByText("Acme renewal").closest("article");
    expect(document.activeElement).toBe(moved);
  });

  it("read_only_has_no_menu_and_no_drag", () => {
    render(<Board readOnly />);
    expect(screen.queryByRole("button", { name: /More actions|Move to/ })).toBeNull();
    expect(screen.getByText("Acme renewal").closest("article")?.getAttribute("draggable")).toBe("false");
  });

  it("column_header_says_12_of_40, and offers Show more for the rest", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();
    const many = Array.from({ length: 12 }, (_, i) => ({ id: `d${i}`, title: `Deal ${i}`, stage: "doing" }));
    const { rerender } = render(
      <Board columns={[{ id: "doing", label: "Doing", total: 40 }]} items={many} onLoadMore={onLoadMore} />,
    );
    expect(within(column("Doing")).getByText("12 of 40")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Show more in Doing" }));
    expect(onLoadMore).toHaveBeenCalledWith("doing");

    rerender(
      <Board
        columns={[{ id: "doing", label: "Doing", total: 40 }]}
        items={many}
        onLoadMore={onLoadMore}
        loadingColumnIds={new Set(["doing"])}
      />,
    );
    expect(screen.getByRole("button", { name: "Show more in Doing" }).getAttribute("aria-busy")).toBe("true");
  });

  it("shows an empty column's own label", () => {
    render(<Board columns={[...COLUMNS.slice(0, 2), { id: "lost", label: "Lost", emptyLabel: "No lost deals" }]} />);
    expect(within(column("Lost")).getByText("No lost deals")).toBeTruthy();
    expect(within(column("To do")).queryByText("Nothing here")).toBeNull();
  });
});
