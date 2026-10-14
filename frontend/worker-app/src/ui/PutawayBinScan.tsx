import { useEffect, useRef, useState } from "react";

import { Button } from "@opsgrids/ui";

import { barcodeProblem, cleanBarcode } from "@/lib/putawayForm";
import { ScanCamera } from "@/ui/ScanCamera";
import { ScanInput } from "@/ui/ScanInput";

interface PutawayBinScanProps {
  /** The accepted label, or `null` while one is still being looked for. */
  label: string | null;
  onAccept: (label: string) => void;
  onClear: () => void;
}

/**
 * Step three: the bin label. The camera (`BarcodeDetector`, where the browser has it), a hardware
 * scanner typing into the field, or the keyboard all land in the same place — a label, trimmed of
 * the CR/LF a scanner adds, that the worker accepts before anything is sent. The server resolves it
 * to a bin; nothing here guesses which bin it is.
 */
export function PutawayBinScan({ label, onAccept, onClear }: PutawayBinScanProps) {
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | undefined>(undefined);
  const fieldRef = useRef<HTMLInputElement>(null);
  const acceptedRef = useRef<HTMLDivElement>(null);

  // This step mounts when stock is chosen, and swaps one control for another each time the label
  // is accepted or changed — so it says where focus goes. Looking for a label: the field, because a
  // keyboard-wedge scanner types into whatever has focus. Accepted: the label itself, a place a
  // scanner's trailing Enter does nothing, so a second read can never confirm the put-away.
  //
  // The field comes back holding the label that was just changed or refused, so a person can fix a
  // typo in it — and with that text *selected*, because a scanner types at the caret: left
  // unselected, the next scan would land on the end of the old label and make a third one.
  useEffect(() => {
    if (label === null) {
      fieldRef.current?.focus();
      fieldRef.current?.select();
    } else acceptedRef.current?.focus();
  }, [label]);

  function accept(raw: string) {
    const cleaned = cleanBarcode(raw);
    const invalid = barcodeProblem(cleaned);
    setDraft(cleaned);
    if (invalid) {
      setProblem(invalid);
      return;
    }
    setProblem(undefined);
    onAccept(cleaned);
  }

  if (label !== null) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-tile bg-ground p-3">
        <div ref={acceptedRef} tabIndex={-1} className="flex min-w-0 flex-col gap-1">
          <p className="text-xs font-semibold text-muted">Bin label</p>
          <p className="truncate font-mono text-lg font-bold text-ink">{label}</p>
        </div>
        <Button variant="secondary" className="min-h-12" aria-label="Change bin label" onClick={onClear}>
          Change
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <ScanCamera onDetected={accept} subject="the bin's label" videoLabel="Camera view for scanning the bin label" />
      <ScanInput
        ref={fieldRef}
        label="Bin label"
        value={draft}
        onChange={(value) => {
          setDraft(value);
          setProblem(undefined);
        }}
        onSubmit={() => accept(draft)}
        placeholder="Scan the bin label, or type it and press enter"
        {...(problem !== undefined ? { error: problem } : {})}
      />
      <Button variant="secondary" className="min-h-12" onClick={() => accept(draft)} disabled={draft.trim() === ""}>
        Use this label
      </Button>
    </div>
  );
}
