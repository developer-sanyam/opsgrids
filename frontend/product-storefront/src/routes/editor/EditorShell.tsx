import { useEffect, useReducer, useRef, useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Card, InlineAlert, useAnnounce } from "@opsgrids/ui";

import type { Category, Product } from "@/api/types";
import { Canvas } from "@/editor/Canvas";
import {
  canRedo,
  canUndo,
  currentPage,
  editorReducer,
  initEditor,
  isDirty,
  selectedSection,
} from "@/editor/document";
import type { Device, EditorAction, EditorState } from "@/editor/document";
import { EditorToolbar } from "@/editor/EditorToolbar";
import { SectionPalette } from "@/editor/SectionPalette";
import { findDef } from "@/editor/sectionLibrary";
import { SettingsInspector } from "@/editor/SettingsInspector";
import { ThemeTokenPanel } from "@/editor/ThemeTokenPanel";
import type { SectionDef, StoreDoc } from "@/theme/schema";

/**
 * The three panes, the toolbar above them, and the one place document edits are turned into
 * announcements.
 *
 * Every mutation goes through `run`, which dispatches the action and then runs the *same* pure
 * reducer a second time to see what the document became. That is what lets a move announce where
 * the section actually landed — after clamping and after the push-down rule settled whatever it
 * displaced — rather than where the user aimed.
 */

interface EditorShellProps {
  store: StoreDoc;
  library: SectionDef[];
  products: Product[];
  categories: Category[];
  /** A courtesy, not enforcement: the server refuses the `PUT` regardless of what this renders. */
  canEdit: boolean;
  onSave: (store: StoreDoc) => Promise<StoreDoc>;
}

export function EditorShell({
  store,
  library,
  products,
  categories,
  canEdit,
  onSave,
}: EditorShellProps) {
  const announce = useAnnounce();
  const [state, dispatch] = useReducer(editorReducer, store, (value) => initEditor(value));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ id: string; token: number } | null>(null);
  // The canvas scales to fit whatever the side panes leave it, so hiding one is a real zoom
  // control on a laptop. Kept here rather than in a store: two components read it, both children.
  const [paletteOpen, setPaletteOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const idSeq = useRef(0);

  const page = currentPage(state);
  const selected = selectedSection(state);
  const dirty = isDirty(state);

  // Leaving with unsaved work is a real loss: nothing here autosaves, and the document lives only
  // in this tab until Save.
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  function newId(): string {
    idSeq.current += 1;
    return `sec-${Date.now().toString(36)}-${idSeq.current}`;
  }

  /** Dispatch, then say what happened — computed from the document the reducer will produce. */
  function run(action: EditorAction, describe?: (next: EditorState) => string | null) {
    dispatch(action);
    if (!describe) return;
    const message = describe(editorReducer(state, action));
    if (message !== null && message !== "") announce(message);
  }

  function nameOf(type: string): string {
    return findDef(library, type)?.name ?? type;
  }

  function placement(next: EditorState, id: string): string | null {
    const section = next.present.pages
      .find((candidate) => candidate.id === next.pageId)
      ?.sections.find((candidate) => candidate.id === id);
    if (!section) return null;
    return `${nameOf(section.type)} at column ${section.layout.x + 1}, row ${
      section.layout.y + 1
    }, ${section.layout.w} columns wide, ${section.layout.h} rows tall`;
  }

  function addSection(def: SectionDef, at?: { x: number; y: number }) {
    const id = newId();
    run(
      at ? { type: "add-section", def, id, at } : { type: "add-section", def, id },
      (next) => {
        const where = placement(next, id);
        return where === null ? null : `${where}. Added.`;
      },
    );
    setFocusRequest({ id, token: idSeq.current });
  }

  async function save() {
    // One save at a time, and never a second one from a double click: the button disables, and
    // this guard covers the keyboard and any race the button cannot see.
    if (saving || !dirty) return;
    setSaveError(null);

    if (!navigator.onLine) {
      setSaveError("You're offline. Your changes are still here — reconnect and save again.");
      return;
    }

    const snapshot = state.present;
    setSaving(true);
    try {
      await onSave(snapshot);
      // The baseline is what was sent, not what is on screen now: an edit made while the save was
      // in flight is still unsaved, and the indicator says so.
      dispatch({ type: "saved", store: snapshot });
      announce("Store saved.");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? (error.problem?.detail ?? error.message)
          : "Your changes couldn't be saved. They're still here — try again.";
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  }

  // `initEditor` selects the first page and `set-page` only ever takes an id from the list, so the
  // one document that lands here is a store with no pages at all — an adopt that produced nothing,
  // or a theme stripped back to none. It is a real state and it gets a real screen.
  if (!page) {
    return (
      <Card className="p-6">
        <InlineAlert tone="warning" title="This store has no pages" live="off">
          There is nothing to lay out yet. Adopting a theme again from the gallery gives the store
          its pages back.
        </InlineAlert>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {!canEdit ? (
        <InlineAlert tone="warning" title="You can't change this store's design" live="off">
          You can see every page and every setting here. Ask an administrator for the storefront
          design permission to edit them.
        </InlineAlert>
      ) : null}

      {saveError !== null ? (
        <InlineAlert
          tone="danger"
          title={saveError}
          action={
            <Button variant="dark" size="sm" onClick={() => void save()} disabled={saving}>
              Try again
            </Button>
          }
        />
      ) : null}

      <Card className="flex flex-col overflow-hidden lg:h-[calc(100dvh-11rem)]">
        <EditorToolbar
          pages={state.present.pages}
          pageId={state.pageId}
          device={state.device}
          dirty={dirty}
          canUndo={canUndo(state)}
          canRedo={canRedo(state)}
          canEdit={canEdit}
          saving={saving}
          paletteOpen={paletteOpen}
          inspectorOpen={inspectorOpen}
          onTogglePalette={() => {
            setPaletteOpen((open) => !open);
            announce(paletteOpen ? "Sections hidden." : "Sections shown.");
          }}
          onToggleInspector={() => {
            setInspectorOpen((open) => !open);
            announce(inspectorOpen ? "Settings hidden." : "Settings shown.");
          }}
          onPage={(pageId) =>
            run({ type: "set-page", pageId }, (next) => {
              const name = next.present.pages.find((candidate) => candidate.id === pageId)?.name;
              return name === undefined ? null : `Editing the ${name} page.`;
            })
          }
          onDevice={(device) =>
            run({ type: "set-device", device }, () => `Showing the ${DEVICE_WORDS[device]} width.`)
          }
          onUndo={() => run({ type: "undo" }, () => "Undone.")}
          onRedo={() => run({ type: "redo" }, () => "Redone.")}
          onSave={() => void save()}
          onDiscard={() =>
            run({ type: "discard" }, () => "Changes discarded. Undo brings them back.")
          }
        />

        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div
            hidden={!paletteOpen}
            className="shrink-0 border-b border-line lg:w-72 lg:border-b-0 lg:border-r"
          >
            <SectionPalette
              library={library}
              page={page}
              selectedId={state.selectedId}
              canEdit={canEdit}
              onAdd={(def) => addSection(def)}
              onSelect={(id) => run({ type: "select", id })}
              onDepth={(id, direction) =>
                run({ type: "reorder-depth", id, direction }, (next) => depthMessage(next, id, nameOf))
              }
            />
          </div>

          <div className="min-h-96 min-w-0 flex-1">
            <Canvas
              page={page}
              library={library}
              tokens={state.present.tokens}
              brand={state.present.brand}
              products={products}
              categories={categories}
              device={state.device}
              selectedId={state.selectedId}
              canEdit={canEdit}
              focusRequest={focusRequest}
              onSelect={(id) => run({ type: "select", id })}
              onMove={(id, x, y) => run({ type: "move-section", id, x, y })}
              onResize={(id, w, h) => run({ type: "resize-section", id, w, h })}
              onNudge={(id, x, y) =>
                run({ type: "move-section", id, x, y }, (next) => placement(next, id))
              }
              onScale={(id, w, h) =>
                run({ type: "resize-section", id, w, h }, (next) => placement(next, id))
              }
              onDepth={(id, direction) =>
                run({ type: "reorder-depth", id, direction }, (next) => depthMessage(next, id, nameOf))
              }
              onRemove={(id) => {
                const label = nameOf(
                  page.sections.find((section) => section.id === id)?.type ?? "Section",
                );
                run({ type: "remove-section", id }, () => `${label} deleted. Undo brings it back.`);
              }}
              onDragEnd={() => {
                if (state.selectedId === null) return;
                const where = placement(state, state.selectedId);
                if (where !== null) announce(where);
              }}
              onDropSection={(def, at) => addSection(def, at)}
            />
          </div>

          <aside
            hidden={!inspectorOpen}
            aria-label="Section and theme settings"
            className="shrink-0 overflow-y-auto border-t border-line p-4 lg:w-80 lg:border-l lg:border-t-0"
          >
            <div className="flex flex-col gap-5">
              <SettingsInspector
                section={selected}
                def={selected ? findDef(library, selected.type) : undefined}
                canEdit={canEdit}
                onSettingChange={(key, value) => {
                  if (!selected) return;
                  run({ type: "set-setting", id: selected.id, key, value });
                }}
                onMove={(x, y) => {
                  if (!selected) return;
                  run({ type: "move-section", id: selected.id, x, y });
                }}
                onResize={(w, h) => {
                  if (!selected) return;
                  run({ type: "resize-section", id: selected.id, w, h });
                }}
                onDuplicate={() => {
                  if (!selected) return;
                  const copyId = newId();
                  run({ type: "duplicate-section", id: selected.id, newId: copyId }, (next) => {
                    const where = placement(next, copyId);
                    return where === null ? null : `${where}. Copied.`;
                  });
                  setFocusRequest({ id: copyId, token: idSeq.current });
                }}
                onRemove={() => {
                  if (!selected) return;
                  const label = nameOf(selected.type);
                  run({ type: "remove-section", id: selected.id }, () =>
                    `${label} deleted. Undo brings it back.`,
                  );
                }}
                onDepth={(direction) => {
                  if (!selected) return;
                  const id = selected.id;
                  run({ type: "reorder-depth", id, direction }, (next) =>
                    depthMessage(next, id, nameOf),
                  );
                }}
              />

              <details className="rounded-tile border border-line">
                <summary className="cursor-pointer rounded-tile px-3 py-2 text-sm font-semibold text-ink">
                  Theme and brand
                </summary>
                <div className="border-t border-line p-3">
                  <ThemeTokenPanel
                    tokens={state.present.tokens}
                    brand={state.present.brand}
                    canEdit={canEdit}
                    onToken={(key, value) => run({ type: "set-token", key, value })}
                    onBrand={(key, value) => run({ type: "set-brand", key, value })}
                  />
                </div>
              </details>
            </div>
          </aside>
        </div>
      </Card>
    </div>
  );
}

const DEVICE_WORDS: Record<Device, string> = {
  desktop: "desktop",
  tablet: "tablet",
  mobile: "phone",
};

function depthMessage(
  next: EditorState,
  id: string,
  nameOf: (type: string) => string,
): string | null {
  const sections = next.present.pages.find((page) => page.id === next.pageId)?.sections;
  if (!sections) return null;
  const index = sections.findIndex((section) => section.id === id);
  const section = sections[index];
  if (index === -1 || !section) return null;
  return `${nameOf(section.type)} is now ${index + 1} of ${sections.length} in the page order.`;
}
