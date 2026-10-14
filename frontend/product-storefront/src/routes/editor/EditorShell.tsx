import { useEffect, useReducer, useRef, useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Card, InlineAlert, TABLET_QUERY, Tabs, useAnnounce, useMediaQuery } from "@opsgrids/ui";

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
import { PageManager } from "@/editor/PageManager";
import { SectionPalette } from "@/editor/SectionPalette";
import { findDef } from "@/editor/sectionLibrary";
import { SettingsInspector } from "@/editor/SettingsInspector";
import { ThemeTokenPanel } from "@/editor/ThemeTokenPanel";
import type { Brand, NavPlacement, SectionDef, StoreDoc } from "@/theme/schema";
import { resolveMode, sectionsWithPlaceholders } from "@/theme/store";
import { StickyActionBar } from "@/ui/StickyActionBar";

type PhonePane = "sections" | "page" | "settings";

const PHONE_PANES: { id: PhonePane; label: string }[] = [
  { id: "sections", label: "Sections" },
  { id: "page", label: "Page" },
  { id: "settings", label: "Settings" },
];

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
  /** The store's name, tagline and header wordmark — read-only here. It lives on the store's own
   *  identity (`/settings`), not on the document this shell edits (ADR-0011 §2). */
  brand: Brand;
  library: SectionDef[];
  products: Product[];
  categories: Category[];
  /** A courtesy, not enforcement: the server refuses the `PUT` regardless of what this renders. */
  canEdit: boolean;
  onSave: (store: StoreDoc) => Promise<StoreDoc>;
}

export function EditorShell({
  store,
  brand,
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
  // A stale `version` conflict is not fixed by resending the same document — the merchant needs
  // the other tab's edit first. Tracked apart from `saveError` so the action offered differs.
  const [saveConflict, setSaveConflict] = useState(false);
  const [focusRequest, setFocusRequest] = useState<{ id: string; token: number } | null>(null);
  // The canvas scales to fit whatever the side panes leave it, so hiding one is a real zoom
  // control on a laptop. Kept here rather than in a store: two components read it, both children.
  const [paletteOpen, setPaletteOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [pagesOpen, setPagesOpen] = useState(false);
  // On a phone three panes cannot sit side by side, nor stack into a page of scrolling: they are
  // tabs, one at a time, with Save kept in a bar at the foot. `true` where there is no
  // `matchMedia` (tests) keeps the desktop layout.
  const roomy = useMediaQuery(TABLET_QUERY, true);
  const [phonePane, setPhonePane] = useState<PhonePane>("page");
  const idSeq = useRef(0);

  // A phone edits the page as a phone shows it: 390 wide and stacked, at close to full size, rather
  // than a 1280px desktop page shrunk to a fifth where nothing can be read or tapped. The presets
  // are not offered here, so this is the width it opens at and stays at. Once, on arrival.
  useEffect(() => {
    if (!roomy) dispatch({ type: "set-device", device: "mobile" });
    // Arrival only: a later resize must not override a width somebody chose on a wider screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  function newId(prefix = "sec"): string {
    idSeq.current += 1;
    return `${prefix}-${Date.now().toString(36)}-${idSeq.current}`;
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

  /*
   * The page actions, and the sentence each one produces.
   *
   * Every one is computed from the document the reducer *will* produce, the same way a section
   * move is: a route the merchant typed is normalised on the way in, so announcing what they typed
   * would be announcing something that did not happen.
   */
  function pageName(next: EditorState, id: string): string | null {
    return next.present.pages.find((candidate) => candidate.id === id)?.name ?? null;
  }

  function addPage(route: string, name: string) {
    const id = newId("page");
    run({ type: "add-page", id, route, name }, (next) => {
      const page = next.present.pages.find((candidate) => candidate.id === id);
      if (!page) return `That address is already taken. The page wasn't added.`;
      return `${page.name} added at ${page.route}. You're editing it now.`;
    });
  }

  function deletePage(id: string) {
    const label = state.present.pages.find((candidate) => candidate.id === id)?.name ?? "Page";
    run({ type: "delete-page", pageId: id }, (next) => {
      const landed = pageName(next, next.pageId);
      const where = landed === null ? "" : ` Now editing ${landed}.`;
      return `${label} deleted. Undo brings it back.${where}`;
    });
  }

  function removeSection(id: string) {
    const label = nameOf(page?.sections.find((section) => section.id === id)?.type ?? "Section");
    run({ type: "remove-section", id }, () => `${label} deleted. Undo brings it back.`);
  }

  async function save() {
    // One save at a time, and never a second one from a double click: the button disables, and
    // this guard covers the keyboard and any race the button cannot see.
    if (saving || !dirty) return;
    setSaveError(null);
    setSaveConflict(false);

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
      if (error instanceof ApiError && error.status === 409) {
        // Someone else saved this store's theme since it loaded here. Resending the same document
        // would silently overwrite their change — the only honest move is a reload, not a retry.
        setSaveConflict(true);
        setSaveError(
          error.problem?.detail ??
            "This store's design changed since you loaded it. Reload to see the latest version before saving again.",
        );
      } else {
        const message =
          error instanceof ApiError
            ? (error.problem?.detail ?? error.message)
            : "Your changes couldn't be saved. They're still here — try again.";
        setSaveError(message);
      }
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

  const palette = (
    <SectionPalette
      library={library}
      page={page}
      selectedId={state.selectedId}
      canEdit={canEdit}
      onAdd={(def) => addSection(def)}
      onSelect={(id) => run({ type: "select", id })}
      onRemove={removeSection}
      onDepth={(id, direction) =>
        run({ type: "reorder-depth", id, direction }, (next) => depthMessage(next, id, nameOf))
      }
    />
  );

  const canvas = (
    <Canvas
      page={page}
      library={library}
      tokens={state.present.tokens}
      brand={brand}
      mode={resolveMode(state.present)}
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
      onRemove={removeSection}
      onDragEnd={() => {
        if (state.selectedId === null) return;
        const where = placement(state, state.selectedId);
        if (where !== null) announce(where);
      }}
      onDropSection={(def, at) => addSection(def, at)}
    />
  );

  const inspector = (
    <div className="flex flex-col gap-5">
      <SettingsInspector
        section={selected}
        def={selected ? findDef(library, selected.type) : undefined}
        categories={categories}
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
            brand={brand}
            canEdit={canEdit}
            onToken={(key, value) => run({ type: "set-token", key, value })}
          />
        </div>
      </details>
    </div>
  );

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
            saveConflict ? (
              <Button variant="dark" size="sm" onClick={() => window.location.reload()}>
                Reload
              </Button>
            ) : (
              <Button variant="dark" size="sm" onClick={() => void save()} disabled={saving}>
                Try again
              </Button>
            )
          }
        />
      ) : null}

      <Card className="flex flex-col overflow-hidden lg:h-[calc(100dvh-11rem)]">
        <EditorToolbar
          pages={state.present.pages}
          pageId={state.pageId}
          device={state.device}
          dirty={dirty}
          placeholderCount={sectionsWithPlaceholders(state.present).length}
          canUndo={canUndo(state)}
          canRedo={canRedo(state)}
          canEdit={canEdit}
          saving={saving}
          phone={!roomy}
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
          onManagePages={() => setPagesOpen(true)}
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

        {roomy ? (
          <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
            <div
              hidden={!paletteOpen}
              className="shrink-0 border-b border-line lg:w-72 lg:border-b-0 lg:border-r"
            >
              {palette}
            </div>

            <div className="min-h-96 min-w-0 flex-1">{canvas}</div>

            <aside
              hidden={!inspectorOpen}
              aria-label="Section and theme settings"
              className="shrink-0 overflow-y-auto border-t border-line p-4 lg:w-80 lg:border-l lg:border-t-0"
            >
              {inspector}
            </aside>
          </div>
        ) : (
          <div className="flex flex-col gap-2 pt-1">
            <div className="px-4">
              <Tabs
                label="Editor panes"
                items={PHONE_PANES}
                value={phonePane}
                onChange={(id) => setPhonePane(id as PhonePane)}
              >
                {phonePane === "sections" ? palette : null}
                {phonePane === "page" ? <div className="min-h-96 min-w-0">{canvas}</div> : null}
                {phonePane === "settings" ? (
                  <div aria-label="Section and theme settings" role="region" className="py-4">
                    {inspector}
                  </div>
                ) : null}
              </Tabs>
            </div>
          </div>
        )}
      </Card>

      {/* Save is the one action the editor exists for, so on a phone it stays in reach under the
          panes however long the Sections list or the settings run. */}
      {!roomy && canEdit ? (
        <StickyActionBar>
          <span className="min-w-0 flex-1 text-sm text-muted" aria-live="polite">
            {saving ? "Saving…" : dirty ? "Unsaved changes" : "All changes saved"}
          </span>
          <Button onClick={() => void save()} disabled={!dirty || saving} loading={saving}>
            Save changes
          </Button>
        </StickyActionBar>
      ) : null}

      <PageManager
        open={pagesOpen}
        onClose={() => setPagesOpen(false)}
        pages={state.present.pages}
        pageId={state.pageId}
        canEdit={canEdit}
        onSelect={(pageId) =>
          run({ type: "set-page", pageId }, (next) => {
            const name = pageName(next, pageId);
            return name === null ? null : `Editing the ${name} page.`;
          })
        }
        onAdd={addPage}
        onRename={(pageId, name) =>
          run({ type: "rename-page", pageId, name }, (next) => {
            const renamed = pageName(next, pageId);
            return renamed === null ? null : `Page renamed to ${renamed}.`;
          })
        }
        onRoute={(pageId, route) =>
          run({ type: "set-page-route", pageId, route }, (next) => {
            const page = next.present.pages.find((candidate) => candidate.id === pageId);
            return page === undefined ? null : `${page.name} now answers ${page.route}.`;
          })
        }
        onNav={(pageId, nav) =>
          run({ type: "set-page-nav", pageId, nav }, (next) => {
            const name = pageName(next, pageId);
            return name === null ? null : `${name} is ${NAV_WORDS[nav]}.`;
          })
        }
        onReorder={(pageId, direction) =>
          run({ type: "reorder-pages", pageId, direction }, (next) => {
            const index = next.present.pages.findIndex((candidate) => candidate.id === pageId);
            const page = next.present.pages[index];
            if (index === -1 || !page) return null;
            return `${page.name} is now ${index + 1} of ${next.present.pages.length} in the navigation order.`;
          })
        }
        onDelete={deletePage}
      />
    </div>
  );
}

const NAV_WORDS: Record<NavPlacement, string> = {
  primary: "in the top navigation",
  footer: "in the footer",
  none: "not in the navigation — link to it from a section instead",
};

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
