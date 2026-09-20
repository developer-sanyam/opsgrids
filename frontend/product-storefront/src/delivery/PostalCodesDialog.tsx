import { useCallback, useRef, useState } from "react";

import { ApiError, useAsync } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, PaperclipIcon, Skeleton, TextArea } from "@opsgrids/ui";

import { listZonePostalCodes, loadPostalCodeMap, saveZonePostalCodes } from "./api";
import { DeliveryProblem } from "./DeliveryProblem";
import { PlanSummary } from "./PlanSummary";
import { parsePostalCodeText, planIsNoop, planZoneSave } from "./postalCodes";
import type { SavePlan } from "./postalCodes";
import type { ZoneOut, ZonePostalCodesOut } from "./types";

/**
 * One zone's postal codes: the textarea a merchant actually uses.
 *
 * A merchant onboarding a city **pastes a list**. They do not click two hundred times, so the
 * primary control is a textarea that takes one-per-line, comma-separated, tab-separated or all
 * three, and a CSV picker that loads a column into the same box rather than into a different
 * workflow.
 *
 * `PUT` carries the whole set (ADR-0010 §9), so what is missing from the box is removed from the
 * zone. That is stated in the preview as a count before anything is sent, not discovered
 * afterwards in an audit row.
 *
 * **One `Dialog`, mounted once, for every state this screen has.** It started as two — a loading
 * one that swapped for an editor one — and that cost a real thing: `Dialog` captures the element
 * focus came from when it mounts, so the editor captured the *loading dialog's heading*, and
 * closing it sent a keyboard user to the page container instead of back to the "Postal codes"
 * button they pressed. Verified in Chrome by tabbing. One mount, and the actions live in the
 * dialog's `footer` slot, which is pinned to the bottom of the sheet on a phone.
 */
export function PostalCodesDialog({
  open,
  zone,
  zones,
  onClose,
  onSaved,
}: {
  open: boolean;
  zone: ZoneOut;
  zones: readonly ZoneOut[];
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const load = useCallback(async () => {
    // The zone's own codes seed the box; the whole map is what makes "moving out of Andheri West"
    // sayable. Both are a snapshot — the server recomputes and returns its own counters.
    const [codes, map] = await Promise.all([
      listZonePostalCodes(zone.id),
      loadPostalCodeMap(zones),
    ]);
    return { codes, map };
  }, [zone.id, zones]);

  // A stable dependency: `zones` is a fresh array on every parent render, and keying the effect on
  // its identity would refetch the whole map in a loop.
  const zoneKey = zones.map((candidate) => candidate.id).join(",");
  const state = useAsync(load, [zone.id, zoneKey]);

  /**
   * `null` means "the merchant has not touched the box", and the zone's current codes are shown
   * instead. It is **not** `""`: an empty box is a merchant who deliberately cleared it, which
   * means "remove every code from this zone", and the two must never look the same.
   *
   * Derived rather than seeded in an effect. An effect would render the box empty for one frame
   * and then fill it — a flash on every open, and one frame in which the plan claimed the save
   * would drop every code in the zone.
   */
  const [text, setText] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ step: "editing" });
  const [failure, setFailure] = useState<string | null>(null);
  const [fileNote, setFileNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const map = state.status === "success" ? state.data.map : null;
  const value =
    text ?? (state.status === "success" ? state.data.codes.postal_codes.join("\n") : "");
  const plan: SavePlan | null = map !== null ? planZoneSave(value, zone.id, map) : null;
  const saving = phase.step === "saving";
  const noop = plan === null || planIsNoop(plan);

  async function onFile(file: File) {
    setFailure(null);
    try {
      // Read in the browser and drop the codes into the box the merchant can still edit. Posting
      // the file straight at the server would skip the preview, which is the point of the screen.
      const raw = await file.text();
      const parsed = parsePostalCodeText(raw);
      const found = [...new Set(parsed.codes.map((code) => code.value))];
      setText((current) => {
        const existing = (current ?? value).trim();
        return existing === "" ? found.join("\n") : `${existing}\n${found.join("\n")}`;
      });
      setFileNote(
        `${file.name}: ${found.length} postal ${found.length === 1 ? "code" : "codes"} added to the box${
          parsed.rejected.length > 0 ? `, ${parsed.rejected.length} not recognised` : ""
        }. Nothing is saved yet.`,
      );
    } catch {
      setFailure("That file couldn't be read. Save it as CSV or plain text and try again.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function save() {
    if (saving || plan === null) return;
    setPhase({ step: "saving" });
    setFailure(null);
    try {
      // The raw text goes over the wire, not the parsed list — the server owns the parse
      // (`ZonePostalCodesPut.postal_codes` is a string). The plan travels alongside only so the
      // report can say what was *asked for* beside what the server ended up holding.
      const result = await saveZonePostalCodes(zone.id, value);
      setPhase({ step: "saved", result, requested: plan });
      onSaved(`${zone.name} now holds ${result.total} postal codes.`);
    } catch (error) {
      // The textarea is untouched — the merchant's list survives a failed save.
      setPhase({ step: "editing" });
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Couldn't reach the server. Nothing was saved — your list is still here.",
      );
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Postal codes — ${zone.name}`}
      description="Paste one per line or comma-separated. Nothing is written until you have seen what it would do."
      size="lg"
      footer={
        phase.step === "saved" ? (
          <Button onClick={onClose}>Done</Button>
        ) : state.status === "success" ? (
          <>
            <Button variant="ghost" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={() => void save()} disabled={saving || noop}>
              {saving ? "Saving…" : noop ? "Nothing to save" : "Save these postal codes"}
            </Button>
          </>
        ) : null
      }
    >
      {state.status === "loading" ? (
        /* Reserves the editor's height so the dialog does not resize under the pointer when the
           codes land. */
        <div className="flex flex-col gap-3">
          <Skeleton className="h-40 w-full rounded-tile" />
          <Skeleton className="h-9 w-48" />
        </div>
      ) : null}

      {state.status === "error" ? (
        <DeliveryProblem error={state.error} subject="This zone's postal codes" onRetry={onClose} />
      ) : null}

      {phase.step === "saved" ? (
        <SaveReport result={phase.result} requested={phase.requested} />
      ) : null}

      {state.status === "success" && phase.step !== "saved" ? (
        <div className="flex flex-col gap-4">
          {failure !== null ? (
            <InlineAlert tone="danger" title="That didn't save">
              {failure}
            </InlineAlert>
          ) : null}

          <TextArea
            label="Postal codes"
            value={value}
            onChange={(event) => setText(event.target.value)}
            rows={8}
            disabled={saving}
            helper="One per line, or separated by commas. Spaces, hyphens and lower case are tidied up for you."
            placeholder={"400050\n400051, 400052"}
            className="tnum"
          />

          <div className="flex flex-wrap items-center gap-3">
            {/* A real file input with a real label, not a button that clicks a hidden input: the
                native control is keyboard- and screen-reader-complete and opens the platform
                picker. */}
            <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-full border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-ground focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2">
              <PaperclipIcon className="size-4" aria-hidden />
              Load from a CSV
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv,text/plain"
                className="sr-only"
                disabled={saving}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void onFile(file);
                }}
              />
            </label>
            <p className="text-xs text-muted">Any column of postal codes. A header row is ignored.</p>
          </div>

          {/* The file's result is a change nobody clicked into view, so it is announced. */}
          <p aria-live="polite" className="text-sm text-ink-soft empty:hidden">
            {fileNote ?? ""}
          </p>

          {plan !== null ? (
            <div className="border-t border-line pt-4">
              <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-faint">
                What saving would do
              </h3>
              <PlanSummary plan={plan} />
            </div>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

type Phase =
  | { step: "editing" }
  | { step: "saving" }
  | { step: "saved"; result: ZonePostalCodesOut; requested: SavePlan };

/**
 * What the zone holds **now**, straight from the server's response — and, beside it and labelled
 * as such, what this browser asked for.
 *
 * `ZonePostalCodesOut` is the resulting set, not a set of counters: the schema has no
 * added/moved/removed on this route (it has them on the CSV import). So the authoritative number
 * here is `total`, and the breakdown is labelled as the *request* rather than dressed up as a
 * server report. Presenting the preview's numbers as the server's would be the easy lie, and the
 * one a merchant could not catch.
 */
function SaveReport({ result, requested }: { result: ZonePostalCodesOut; requested: SavePlan }) {
  return (
    <div className="flex flex-col gap-4">
      <InlineAlert tone="success" title="Saved">
        {result.zone_code} now holds <span className="font-semibold">{result.total}</span> postal{" "}
        {result.total === 1 ? "code" : "codes"}.
      </InlineAlert>
      <div className="flex flex-col gap-1 text-sm text-ink-soft">
        <p className="font-semibold text-ink">What this save asked for</p>
        <p>
          {requested.added.length} new, {requested.moves.length} moved in from another zone,{" "}
          {requested.removed.length} dropped.
        </p>
        {requested.rejected.length > 0 ? (
          <p>{requested.rejected.length} were not sent — they are not six-digit PIN codes.</p>
        ) : null}
      </div>
    </div>
  );
}
