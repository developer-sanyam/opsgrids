/**
 * Leaving the page while an import runs unmounts the screen, and the data-exchange hook stops a run
 * whose screen is gone: five rows in, the import would end with no warning and no summary. Browser
 * Back and a link are route changes, which the browser's own prompt never sees, so the dialog holds
 * them while rows are going out and asks. These tests drive the router the way the apps build it (a
 * data router) and change the import's state by hand, as the hook would.
 */

import { useSyncExternalStore } from "react";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, RouterProvider, createMemoryRouter } from "react-router-dom";

import type { ImportExportViewActions, ImportExportViewState } from "../lib/importExportView";
import {
  META,
  MENU_STATE,
  PREVIEW_WITH_ERRORS,
  PROGRESS_RUNNING,
  SUMMARY_CLEAN,
  stateFor,
} from "../story-import-export";
import { ImportExportButton } from "./ImportExportButton";

const IMPORTING = stateFor({ phase: "importing", progress: PROGRESS_RUNNING });
const QUESTION = "Stop the import and leave?";

function makeActions(): ImportExportViewActions {
  return {
    setMode: vi.fn(async () => undefined),
    openFile: vi.fn(async () => undefined),
    startImport: vi.fn(async () => undefined),
    cancelImport: vi.fn(),
    reset: vi.fn(),
    downloadTemplate: vi.fn(async () => undefined),
    exportAll: vi.fn(async () => undefined),
    cancelExport: vi.fn(),
    downloadReport: vi.fn(async () => undefined),
  };
}

/** The import's state, held outside the router so a test can move it on while the screen is up. */
function createStore(initial: ImportExportViewState) {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next: ImportExportViewState) {
      value = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

type Store = ReturnType<typeof createStore>;

function Companies({ store, actions }: { store: Store; actions: ImportExportViewActions }) {
  const state = useSyncExternalStore(store.subscribe, store.get);
  return (
    <main>
      <h1>Companies</h1>
      <ImportExportButton meta={META} state={state} actions={actions} canImport canExport />
    </main>
  );
}

/** The screen under a data router, as every app builds one, with the dialog open. */
async function openInRouter(initial: ImportExportViewState = IMPORTING) {
  const user = userEvent.setup();
  const store = createStore(initial);
  const actions = makeActions();
  const router = createMemoryRouter(
    [
      { path: "/deals", element: <h1>Deals</h1> },
      { path: "/companies", element: <Companies store={store} actions={actions} /> },
    ],
    { initialEntries: ["/deals", "/companies"], initialIndex: 1 },
  );
  render(<RouterProvider router={router} />);
  await user.click(screen.getByRole("button", { name: "Import / Export" }));
  const navigate = (to: string | number) =>
    act(async () => {
      await router.navigate(to as string);
    });
  return { user, store, actions, router, navigate };
}

function question() {
  return screen.getByRole("dialog", { name: QUESTION });
}

describe("ImportExportButton: leaving the page during an import", () => {
  it("holds a link to another page while rows are going out, and says what leaving costs", async () => {
    const { router, navigate } = await openInRouter();
    await navigate("/deals");

    expect(router.state.location.pathname).toBe("/companies");
    expect(question().textContent).toContain(
      "An import is running. Leaving now stops it after the row being sent; rows already sent stay created.",
    );
    expect(screen.getByRole("button", { name: "Leave and stop" })).toBeTruthy();
    // Stay is under the fingers, so a stray Enter keeps the import.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Stay" }));
  });

  it("holds browser Back the same way", async () => {
    const { router, navigate } = await openInRouter();
    await navigate(-1);

    expect(router.state.location.pathname).toBe("/companies");
    expect(question()).toBeTruthy();
  });

  it("keeps the import and the page on Stay", async () => {
    const { user, router, actions, navigate } = await openInRouter();
    await navigate("/deals");
    await user.click(screen.getByRole("button", { name: "Stay" }));

    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
    expect(router.state.location.pathname).toBe("/companies");
    expect(screen.getByRole("dialog", { name: "Importing companies" })).toBeTruthy();
    expect(actions.cancelImport).not.toHaveBeenCalled();
    expect(actions.reset).not.toHaveBeenCalled();
  });

  it("means Stay on Escape, and leaves the import dialog beneath the question open", async () => {
    const { user, router, actions, navigate } = await openInRouter();
    await navigate("/deals");
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Importing companies" })).toBeTruthy();
    expect(router.state.location.pathname).toBe("/companies");
    expect(actions.cancelImport).not.toHaveBeenCalled();
  });

  it("stops the import once, then goes where the user was going, on Leave and stop", async () => {
    const { user, router, actions, navigate } = await openInRouter();
    await navigate("/deals");
    await user.click(screen.getByRole("button", { name: "Leave and stop" }));

    expect(actions.cancelImport).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/deals");
    expect(screen.getByRole("heading", { name: "Deals" })).toBeTruthy();
  });

  it("goes back, too, on Leave and stop", async () => {
    const { user, router, actions, navigate } = await openInRouter();
    await navigate(-1);
    await user.click(screen.getByRole("button", { name: "Leave and stop" }));

    expect(actions.cancelImport).toHaveBeenCalledTimes(1);
    expect(router.state.location.pathname).toBe("/deals");
  });

  it("holds from the moment the import starts, not from when the dialog was opened", async () => {
    const { store, router, navigate } = await openInRouter(stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS }));
    act(() => store.set(IMPORTING));
    await navigate("/deals");

    expect(router.state.location.pathname).toBe("/companies");
    expect(question()).toBeTruthy();
  });

  it.each([
    ["the menu", MENU_STATE],
    ["reading", stateFor({ phase: "reading" })],
    ["the preview", stateFor({ phase: "preview", preview: PREVIEW_WITH_ERRORS })],
    ["the result", stateFor({ phase: "done", summary: SUMMARY_CLEAN })],
    ["an error", stateFor({ phase: "error", error: "That file is 7 MB. The limit is 5 MB." })],
  ])("lets a navigation through untouched on %s, where nothing is being sent", async (_, state) => {
    const { router, actions, navigate } = await openInRouter(state);
    await navigate("/deals");

    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
    expect(router.state.location.pathname).toBe("/deals");
    expect(actions.cancelImport).not.toHaveBeenCalled();
  });

  it("lets through a change to the page's own query string, which does not unmount the screen", async () => {
    const { router, actions, navigate } = await openInRouter();
    await navigate("/companies?page=2");

    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
    expect(router.state.location.search).toBe("?page=2");
    expect(screen.getByRole("dialog", { name: "Importing companies" })).toBeTruthy();
    expect(actions.cancelImport).not.toHaveBeenCalled();
  });

  it("takes the question down when the import finishes underneath it, and shows the result", async () => {
    const { store, router, actions, navigate } = await openInRouter();
    await navigate("/deals");
    expect(question()).toBeTruthy();

    act(() => store.set(stateFor({ phase: "done", summary: SUMMARY_CLEAN })));
    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
    expect(router.state.location.pathname).toBe("/companies");
    expect(screen.getByRole("heading", { name: "Import finished" })).toBeTruthy();
    expect(actions.cancelImport).not.toHaveBeenCalled();
  });

  it("is not there once the dialog is closed", async () => {
    const { user, store, router, navigate } = await openInRouter(stateFor({ phase: "done", summary: SUMMARY_CLEAN }));
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // A second import begins elsewhere in the state; with no dialog nothing is watching for leaving.
    act(() => store.set(IMPORTING));
    await navigate("/deals");
    expect(router.state.location.pathname).toBe("/deals");
  });
});

describe("ImportExportButton: where the router cannot hold a navigation", () => {
  it("renders and runs the import without a router at all", async () => {
    const user = userEvent.setup();
    render(<ImportExportButton meta={META} state={IMPORTING} actions={makeActions()} canImport canExport />);
    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    expect(screen.getByRole("dialog", { name: "Importing companies" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
  });

  it("does the same under a router that is not a data router, where useBlocker would throw", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ImportExportButton meta={META} state={IMPORTING} actions={makeActions()} canImport canExport />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "Import / Export" }));
    expect(screen.getByRole("dialog", { name: "Importing companies" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: QUESTION })).toBeNull();
  });
});
