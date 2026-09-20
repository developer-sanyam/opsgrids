import { Button, Select, Tag } from "@opsgrids/ui";

import type { PageDoc } from "@/theme/schema";
import { DeviceToggle } from "@/ui/DeviceToggle";

import type { Device } from "./document";

/**
 * The bar above the three panes: which page, how wide, what has changed, and what to do about it.
 *
 * Save is the only primary action on the screen. Undo and redo are quiet outline buttons that
 * disable when there is nothing to undo — disabled rather than hidden, because their position is
 * how a user learns where they are, and unlike a permission gate the reason is obvious.
 */

interface EditorToolbarProps {
  pages: PageDoc[];
  pageId: string;
  device: Device;
  dirty: boolean;
  canUndo: boolean;
  canRedo: boolean;
  canEdit: boolean;
  saving: boolean;
  paletteOpen: boolean;
  inspectorOpen: boolean;
  onPage: (pageId: string) => void;
  onManagePages: () => void;
  onDevice: (device: Device) => void;
  onTogglePalette: () => void;
  onToggleInspector: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => void;
  onDiscard: () => void;
}

export function EditorToolbar({
  pages,
  pageId,
  device,
  dirty,
  canUndo,
  canRedo,
  canEdit,
  saving,
  paletteOpen,
  inspectorOpen,
  onPage,
  onManagePages,
  onDevice,
  onTogglePalette,
  onToggleInspector,
  onUndo,
  onRedo,
  onSave,
  onDiscard,
}: EditorToolbarProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 border-b border-line bg-surface px-6 py-3">
      <div className="flex flex-wrap items-end gap-4">
        <Select
          label="Page"
          value={pageId}
          onChange={(event) => onPage(event.target.value)}
          options={pages.map((page) => ({ value: page.id, label: `${page.name} — ${page.route}` }))}
          containerClassName="min-w-56"
        />

        {/* The select switches; everything else a page can have done to it — added, renamed,
            re-routed, reordered, placed in the navigation, deleted — is behind here. Splitting
            them keeps the one-handed act of "show me the other page" a single control. */}
        <div className="pb-1">
          <Button variant="outline" size="sm" onClick={onManagePages}>
            Pages
          </Button>
        </div>

        <div className="pb-1">
          <DeviceToggle value={device} onChange={onDevice} />
        </div>

        {/* The canvas is scaled to whatever room is left after these two panes, so hiding one is
            how a merchant on a laptop gets the page closer to full size. The label states what
            pressing it will do, which is why there is no `aria-pressed` here to contradict it. */}
        <div className="flex items-center gap-2 pb-1">
          <Button variant="ghost" size="sm" onClick={onTogglePalette}>
            {paletteOpen ? "Hide sections" : "Show sections"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onToggleInspector}>
            {inspectorOpen ? "Hide settings" : "Show settings"}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 pb-1">
        {dirty ? (
          <Tag tone="warning">Unsaved changes</Tag>
        ) : (
          <Tag tone="neutral">All changes saved</Tag>
        )}

        {canEdit ? (
          <>
            <Button variant="outline" size="sm" onClick={onUndo} disabled={!canUndo}>
              Undo
            </Button>
            <Button variant="outline" size="sm" onClick={onRedo} disabled={!canRedo}>
              Redo
            </Button>
            <Button variant="ghost" size="sm" onClick={onDiscard} disabled={!dirty || saving}>
              Discard
            </Button>
            <Button variant="primary" size="sm" onClick={onSave} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save changes"}
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}
