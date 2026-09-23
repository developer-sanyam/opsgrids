import { useEffect, useLayoutEffect, useRef, useState } from "react";
// Aliased: `useRowEscape` below listens on `window` and needs the DOM's `KeyboardEvent`, which
// React's same-named type would shadow.
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { Button, cn, Dialog, Select, Tag, TextField } from "@opsgrids/ui";

import type { NavPlacement, PageDoc } from "@/theme/schema";
import { navPlacement } from "@/theme/store";

import {
  checkRoute,
  isStructuralRoute,
  MAX_PAGE_NAME_LENGTH,
  nameFromRoute,
  normalisePageName,
  normaliseRoute,
  structuralReason,
} from "./pageRoutes";

/**
 * Every page in the store, and the six things a merchant can do to one.
 *
 * The toolbar's `Page` select is the switcher and stays exactly what it was. This is the manager
 * behind it — add, rename, re-route, reorder, place in the navigation, delete — and it exists
 * because a ~120-page sitemap cannot start from an editor whose page list is fixed at whatever
 * three pages a theme happened to ship.
 *
 * **Everything here works from the keyboard.** The rows are buttons, reordering is Alt with an
 * arrow key exactly as it is in the section list, and the delete confirmation is an inline step
 * inside this dialog rather than a second dialog on top of it — two focus traps listening on the
 * document at once is how Escape starts closing the wrong thing.
 */

interface PageManagerProps {
  open: boolean;
  onClose: () => void;
  pages: PageDoc[];
  /** The page the editor is on. */
  pageId: string;
  canEdit: boolean;
  onSelect: (pageId: string) => void;
  onAdd: (route: string, name: string) => void;
  onRename: (pageId: string, name: string) => void;
  onRoute: (pageId: string, route: string) => void;
  onNav: (pageId: string, nav: NavPlacement) => void;
  onReorder: (pageId: string, direction: "up" | "down") => void;
  onDelete: (pageId: string) => void;
}

const NAV_OPTIONS = [
  { value: "primary", label: "In the top navigation" },
  { value: "footer", label: "In the footer" },
  { value: "none", label: "Not in the navigation" },
];

const NAV_TAGS: Record<NavPlacement, { label: string; tone: "info" | "neutral" }> = {
  primary: { label: "Top navigation", tone: "info" },
  footer: { label: "Footer", tone: "neutral" },
  none: { label: "Not in the navigation", tone: "neutral" },
};

/** The `name` attributes the focus helpers below find a field by. */
const NAME_FIELD = "page-name";
const ROUTE_FIELD = "page-route";

const ROUTE_HELPER = "Lowercase letters, numbers and hyphens — “/about-us”.";

function sectionCount(page: PageDoc): string {
  if (page.sections.length === 0) return "No sections yet";
  return page.sections.length === 1 ? "1 section" : `${page.sections.length} sections`;
}

/**
 * Escape backs out of the row, not out of the dialog.
 *
 * `Dialog` traps focus with a `keydown` listener on `document` in the capture phase, so a React
 * handler on the row — which runs on the bubble, at the root — cannot stop it: by then the dialog
 * has already closed. A capture listener on `window` is genuinely earlier in the propagation path
 * (window, then document), which is what makes `stopPropagation` here reach it.
 *
 * Mounted only while a row is open, so Escape means "close the dialog" everywhere else.
 */
function useRowEscape(onEscape: () => void) {
  const latest = useRef(onEscape);
  useEffect(() => {
    latest.current = onEscape;
  });
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      latest.current();
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);
}

/** Which row, if any, has swapped out of its resting state. Only ever one at a time. */
type RowMode =
  | { kind: "idle" }
  | { kind: "edit"; pageId: string }
  | { kind: "delete"; pageId: string };

export function PageManager({
  open,
  onClose,
  pages,
  pageId,
  canEdit,
  onSelect,
  onAdd,
  onRename,
  onRoute,
  onNav,
  onReorder,
  onDelete,
}: PageManagerProps) {
  const [mode, setMode] = useState<RowMode>({ kind: "idle" });
  const bodyRef = useRef<HTMLDivElement>(null);
  const initialFocus = useRef<HTMLElement | null>(null);

  /*
   * A layout effect, not a passive one: `Dialog`'s own focus effect reads `initialFocusRef.current`
   * as soon as it mounts, and React runs every layout effect in a commit before any passive one.
   * A `useEffect` here would lose that race and leave focus on the heading.
   *
   * The field is found by its `name` rather than held in a ref because `TextField` renders the
   * input itself and does not forward one — the same shape `DecisionDialog` uses in HRMS.
   */
  useLayoutEffect(() => {
    if (!open) return;
    initialFocus.current = canEdit
      ? (bodyRef.current?.querySelector<HTMLInputElement>(`input[name="${NAME_FIELD}"]`) ?? null)
      : null;
  }, [open, canEdit]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Pages"
      description="Every page in your store. The order here is the order they appear in the navigation."
      size="lg"
      initialFocusRef={initialFocus}
      footer={
        <Button variant="outline" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className="flex flex-col gap-6" ref={bodyRef}>
        {canEdit ? <AddPageForm pages={pages} onAdd={onAdd} /> : null}

        <section className="flex flex-col gap-2" aria-labelledby="page-list-heading">
          <div className="flex flex-col gap-1">
            <h3 id="page-list-heading" className="text-sm font-bold text-ink">
              Pages in this store
            </h3>
            <p className="text-xs text-muted">
              {canEdit
                ? "Alt with up or down moves a page through the order. Choosing a page opens it on the canvas."
                : "Choosing a page opens it on the canvas."}
            </p>
          </div>

          <ol className="flex flex-col gap-2">
            {pages.map((page, index) => (
              <li key={page.id} className="rounded-tile border border-line">
                {mode.kind === "edit" && mode.pageId === page.id ? (
                  <EditPageRow
                    page={page}
                    pages={pages}
                    onRename={onRename}
                    onRoute={onRoute}
                    onDone={() => setMode({ kind: "idle" })}
                  />
                ) : mode.kind === "delete" && mode.pageId === page.id ? (
                  <DeletePageRow
                    page={page}
                    onConfirm={() => {
                      setMode({ kind: "idle" });
                      onDelete(page.id);
                    }}
                    onCancel={() => setMode({ kind: "idle" })}
                  />
                ) : (
                  <PageRow
                    page={page}
                    index={index}
                    total={pages.length}
                    current={page.id === pageId}
                    canEdit={canEdit}
                    onSelect={() => onSelect(page.id)}
                    onNav={(nav) => onNav(page.id, nav)}
                    onReorder={(direction) => onReorder(page.id, direction)}
                    onEdit={() => setMode({ kind: "edit", pageId: page.id })}
                    onDelete={() => setMode({ kind: "delete", pageId: page.id })}
                  />
                )}
              </li>
            ))}
          </ol>
        </section>
      </div>
    </Dialog>
  );
}

/* ----------------------------------- adding ----------------------------------- */

/**
 * Name first, address derived from it — until the merchant edits the address, after which it is
 * theirs and typing in the name field stops rewriting it under them.
 */
function AddPageForm({ pages, onAdd }: { pages: PageDoc[]; onAdd: (route: string, name: string) => void }) {
  const [name, setName] = useState("");
  const [route, setRoute] = useState("");
  const [routeEdited, setRouteEdited] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  // Either field alone is enough — a name gives the address, an address gives the name — so
  // neither is marked optional and neither can be wrong on its own.
  const suggested = routeEdited ? route : name.trim() === "" ? "" : normaliseRoute(name);

  function focusField(field: string) {
    formRef.current?.querySelector<HTMLInputElement>(`input[name="${field}"]`)?.focus();
  }

  function submit() {
    const check = checkRoute(suggested, pages);
    setRouteError(check.error);
    if (check.error !== null) {
      // Focus the field that is wrong: the message is tied to it by `aria-describedby`, so landing
      // there is what reads it out. Nothing typed is cleared — not the name, not the address.
      focusField(ROUTE_FIELD);
      return;
    }

    const cleanName = normalisePageName(name);
    onAdd(check.route, cleanName === "" ? nameFromRoute(check.route) : cleanName);
    setName("");
    setRoute("");
    setRouteEdited(false);
    focusField(NAME_FIELD);
  }

  return (
    <form
      ref={formRef}
      className="flex flex-col gap-3 rounded-tile border border-line bg-ground/40 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <h3 className="text-sm font-bold text-ink">Add a page</h3>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <TextField
          name={NAME_FIELD}
          label="Page name"
          value={name}
          maxLength={MAX_PAGE_NAME_LENGTH}
          helper="What buyers see in the navigation."
          onChange={(event) => {
            setName(event.target.value);
            setRouteError(null);
          }}
          containerClassName="flex-1"
        />
        <TextField
          name={ROUTE_FIELD}
          label="Address"
          value={suggested}
          placeholder="/about-us"
          helper={ROUTE_HELPER}
          {...(routeError !== null ? { error: routeError } : {})}
          onChange={(event) => {
            setRouteEdited(true);
            setRoute(event.target.value);
            setRouteError(null);
          }}
          containerClassName="flex-1"
        />
      </div>
      <div>
        <Button type="submit" variant="primary" size="sm">
          Add page
        </Button>
      </div>
    </form>
  );
}

/* ------------------------------------ a row ----------------------------------- */

function PageRow({
  page,
  index,
  total,
  current,
  canEdit,
  onSelect,
  onNav,
  onReorder,
  onEdit,
  onDelete,
}: {
  page: PageDoc;
  index: number;
  total: number;
  current: boolean;
  canEdit: boolean;
  onSelect: () => void;
  onNav: (nav: NavPlacement) => void;
  onReorder: (direction: "up" | "down") => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const placement = navPlacement(page);
  const tag = NAV_TAGS[placement];
  const locked = isStructuralRoute(page.route);

  function onKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (!canEdit || !event.altKey) return;
    if (event.key === "ArrowUp") {
      event.preventDefault();
      onReorder("up");
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      onReorder("down");
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 p-3">
      <button
        type="button"
        aria-pressed={current}
        /*
         * Spelled out rather than left to the text content.
         *
         * The row shows the position, the name, the address and the section count as adjacent
         * spans with only margins between them — and an accessible name computed from content has
         * no margins in it, so this read as "1Home/·12 sections" in Chrome. Same lesson the design
         * system's `FormField` carries about `(optional)`.
         */
        aria-label={`${page.name} — ${page.route}, ${sectionCount(page)}, ${index + 1} of ${total}`}
        onClick={onSelect}
        onKeyDown={onKeyDown}
        className={cn(
          "flex min-w-0 flex-1 flex-col items-start gap-0.5 rounded-tile px-2 py-1 text-left transition-colors",
          current ? "bg-accent-soft" : "hover:bg-ground",
        )}
      >
        <span className="flex w-full min-w-0 items-center gap-2">
          <span className="tnum text-xs text-faint">{index + 1}</span>
          <span
            className={cn("truncate text-sm", current ? "font-semibold text-accent-ink" : "text-ink")}
          >
            {page.name}
          </span>
        </span>
        <span className="flex w-full min-w-0 items-center gap-2 pl-6 text-xs text-muted">
          <span className="truncate font-mono">{page.route}</span>
          <span aria-hidden>·</span>
          <span>{sectionCount(page)}</span>
        </span>
      </button>

      {canEdit ? (
        <>
          <Select
            label="Navigation"
            // The visible label is one word on every row; the accessible name says which page it
            // belongs to, so a screen reader listing the form's controls does not read thirty-five
            // identical "Navigation" fields.
            aria-label={`Navigation for ${page.name}`}
            value={placement}
            options={NAV_OPTIONS}
            onChange={(event) => onNav(event.target.value as NavPlacement)}
            containerClassName="w-48"
          />
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Move ${page.name} up`}
              disabled={index === 0}
              onClick={() => onReorder("up")}
            >
              Up
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Move ${page.name} down`}
              disabled={index === total - 1}
              onClick={() => onReorder("down")}
            >
              Down
            </Button>
            <Button
              variant="outline"
              size="sm"
              aria-label={`Rename ${page.name}`}
              onClick={onEdit}
            >
              Rename
            </Button>
            {locked ? null : (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Delete ${page.name}`}
                onClick={onDelete}
              >
                Delete
              </Button>
            )}
          </div>
        </>
      ) : (
        <Tag tone={tag.tone}>{tag.label}</Tag>
      )}
    </div>
  );
}

/* --------------------------------- editing a row -------------------------------- */

function EditPageRow({
  page,
  pages,
  onRename,
  onRoute,
  onDone,
}: {
  page: PageDoc;
  pages: PageDoc[];
  onRename: (pageId: string, name: string) => void;
  onRoute: (pageId: string, route: string) => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(page.name);
  const [route, setRoute] = useState(page.route);
  const [nameError, setNameError] = useState<string | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const locked = structuralReason(page.route);
  useRowEscape(onDone);

  function focusField(field: string) {
    formRef.current?.querySelector<HTMLInputElement>(`input[name="${field}"]`)?.focus();
  }

  function submit() {
    const cleanName = normalisePageName(name);
    if (cleanName === "") {
      setNameError("Give the page a name, like “About us”.");
      focusField(NAME_FIELD);
      return;
    }

    // Validate both before changing either: a row that renamed the page and then refused the
    // address would have half-applied the merchant's edit while telling them it failed.
    const check = locked === null ? checkRoute(route, pages, page.id) : null;
    if (check?.error != null) {
      setRouteError(check.error);
      focusField(ROUTE_FIELD);
      return;
    }

    // Only what actually changed is dispatched, so the row never announces an edit it did not
    // make — and when both change, the address is announced last, because it is the riskier one.
    if (cleanName !== page.name) onRename(page.id, cleanName);
    if (check !== null && check.route !== page.route) onRoute(page.id, check.route);
    onDone();
  }

  return (
    <form
      ref={formRef}
      className="flex flex-col gap-3 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <TextField
          name={NAME_FIELD}
          label="Page name"
          // The add form above carries a field with the same visible label. Naming these two for
          // the page they belong to is what keeps a screen reader's list of form controls
          // unambiguous — and what keeps them findable at all.
          aria-label={`Page name for ${page.name}`}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- mounted only by the user's own "Rename" click; moving focus into the field they asked for is the focus management, not a surprise on page load.
          autoFocus
          value={name}
          maxLength={MAX_PAGE_NAME_LENGTH}
          {...(nameError !== null ? { error: nameError } : {})}
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
          containerClassName="flex-1"
        />
        <TextField
          name={ROUTE_FIELD}
          label="Address"
          aria-label={`Address for ${page.name}`}
          value={route}
          disabled={locked !== null}
          helper={locked ?? ROUTE_HELPER}
          {...(routeError !== null ? { error: routeError } : {})}
          onChange={(event) => {
            setRoute(event.target.value);
            setRouteError(null);
          }}
          containerClassName="flex-1"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button type="submit" variant="primary" size="sm">
          Save page
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/* -------------------------------- deleting a row -------------------------------- */

/**
 * The confirmation, inline and specific.
 *
 * It names the page and counts what goes with it, because "Delete this page?" is the same
 * sentence for an empty page and for an afternoon's work, and the merchant is the only one who
 * knows which this is. Undo is named too — it is real here, `delete-page` goes through `commit`
 * like every other edit.
 */
function DeletePageRow({
  page,
  onConfirm,
  onCancel,
}: {
  page: PageDoc;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const count = page.sections.length;
  useRowEscape(onCancel);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-tile bg-negative-soft p-3">
      <p className="min-w-0 flex-1 text-sm text-ink">
        <span className="font-semibold">Delete “{page.name}”?</span>{" "}
        {count === 0
          ? `Nothing is on it yet, and ${page.route} stops working.`
          : count === 1
            ? `The 1 section on it goes too, and ${page.route} stops working.`
            : `All ${count} sections on it go too, and ${page.route} stops working.`}{" "}
        Undo brings it back.
      </p>
      <div className="flex items-center gap-2">
        {/* eslint-disable-next-line jsx-a11y/no-autofocus -- mounted only by the user's own "Delete" click; focus follows them into the confirmation, and Escape (useRowEscape) backs out. */}
        <Button variant="dark" size="sm" autoFocus onClick={onConfirm}>
          Delete page
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Keep it
        </Button>
      </div>
    </div>
  );
}
