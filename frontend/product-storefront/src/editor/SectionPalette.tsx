import { useState } from "react";
import type { DragEvent, KeyboardEvent } from "react";

import { cn, GripIcon, SearchInput } from "@opsgrids/ui";

import type { PageDoc, SectionDef } from "@/theme/schema";
import { SECTION_GROUPS } from "@/theme/sections";

import { SECTION_DRAG_MIME, sectionDragHint } from "./dragTypes";
import { findDef } from "./sectionLibrary";

/**
 * The left pane: every section the theme offers, and the order of the ones already on the page.
 *
 * Both halves work without a pointer. A palette entry is a button — Enter adds it to the bottom of
 * the page — as well as a drag source, and the page order list moves a section with Alt and an
 * arrow key. Drag-and-drop as the *only* way to build a page would put this whole product out of
 * reach of anyone who does not use a mouse.
 */

interface SectionPaletteProps {
  library: SectionDef[];
  page: PageDoc;
  selectedId: string | null;
  canEdit: boolean;
  onAdd: (def: SectionDef) => void;
  onSelect: (id: string) => void;
  onDepth: (id: string, direction: "forward" | "backward") => void;
}

export function SectionPalette({
  library,
  page,
  selectedId,
  canEdit,
  onAdd,
  onSelect,
  onDepth,
}: SectionPaletteProps) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const matches =
    needle === ""
      ? library
      : library.filter((def) => def.name.toLowerCase().includes(needle) || def.type.includes(needle));

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
      {canEdit ? (
        <section className="flex flex-col gap-3" aria-labelledby="palette-heading">
          <div className="flex flex-col gap-1">
            <h2 id="palette-heading" className="text-sm font-bold text-ink">
              Sections
            </h2>
            <p className="text-xs text-muted">
              Drag one onto the page, or press Enter to add it at the bottom.
            </p>
          </div>

          <SearchInput
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            label="Search sections"
            placeholder="Search sections"
          />

          {matches.length === 0 ? (
            <p className="text-xs text-muted">
              No section matches “{query}”. Clear the search to see all {library.length}.
            </p>
          ) : (
            SECTION_GROUPS.map((group) => {
              const items = matches.filter((def) => def.group === group.key);
              if (items.length === 0) return null;
              return (
                <div key={group.key} className="flex flex-col gap-1.5">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">
                    {group.label}
                  </h3>
                  <ul className="flex flex-col gap-1.5">
                    {items.map((def) => (
                      <li key={def.type}>
                        <PaletteItem def={def} onAdd={() => onAdd(def)} />
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })
          )}
        </section>
      ) : null}

      <section className="flex flex-col gap-2" aria-labelledby="outline-heading">
        <div className="flex flex-col gap-1">
          <h2 id="outline-heading" className="text-sm font-bold text-ink">
            On this page
          </h2>
          <p className="text-xs text-muted">
            {canEdit
              ? "Alt with up or down moves a section through the order. On a phone, sections stack in this order."
              : "The sections on this page, in the order they stack on a phone."}
          </p>
        </div>

        {page.sections.length === 0 ? (
          <p className="text-xs text-muted">Nothing on this page yet.</p>
        ) : (
          <ol className="flex flex-col gap-1">
            {page.sections.map((section, index) => {
              const def = findDef(library, section.type);
              return (
                <li key={section.id}>
                  <button
                    type="button"
                    aria-pressed={section.id === selectedId}
                    onClick={() => onSelect(section.id)}
                    onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
                      if (!canEdit || !event.altKey) return;
                      if (event.key === "ArrowUp") {
                        event.preventDefault();
                        onDepth(section.id, "backward");
                      }
                      if (event.key === "ArrowDown") {
                        event.preventDefault();
                        onDepth(section.id, "forward");
                      }
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-tile px-3 py-2 text-left text-sm transition-colors",
                      section.id === selectedId
                        ? "bg-accent-soft text-accent-ink font-semibold"
                        : "text-ink-soft hover:bg-ground",
                    )}
                  >
                    <span className="tnum text-xs text-faint">{index + 1}</span>
                    <span className="truncate">{def?.name ?? section.type}</span>
                    {def ? null : (
                      <span className="ml-auto text-xs text-late">Unknown</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}

function PaletteItem({ def, onAdd }: { def: SectionDef; onAdd: () => void }) {
  function onDragStart(event: DragEvent<HTMLButtonElement>) {
    event.dataTransfer.setData(SECTION_DRAG_MIME, def.type);
    event.dataTransfer.setData(sectionDragHint(def.type), "");
    event.dataTransfer.effectAllowed = "copy";
  }

  return (
    <button
      type="button"
      draggable
      // The visible text is the section's name; what the control *does* is add it. The label
      // keeps the visible name inside the accessible one, which is the rule (WCAG 2.5.3), and
      // still says out loud what pressing it will do.
      aria-label={`Add ${def.name}`}
      onDragStart={onDragStart}
      onClick={onAdd}
      className={cn(
        "flex w-full items-center gap-2 rounded-tile border border-line bg-surface px-3 py-2",
        "text-left text-sm text-ink transition-colors hover:border-accent hover:bg-accent-soft",
        "active:cursor-grabbing",
      )}
    >
      <GripIcon className="size-4 shrink-0 text-faint" aria-hidden />
      <span className="truncate font-medium">{def.name}</span>
      <span className="tnum ml-auto shrink-0 text-xs text-faint">
        {def.defaultLayout.w}×{def.defaultLayout.h}
      </span>
    </button>
  );
}
