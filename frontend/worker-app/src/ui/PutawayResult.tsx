import { useEffect, useRef } from "react";

import { Button, Card, InlineAlert } from "@opsgrids/ui";

import { queuedNotice, warningNotice } from "@/lib/putawayForm";
import type { PutawayCompletion } from "@/lib/putawayForm";
import { formatQty } from "@/lib/quantity";

interface PutawayResultProps {
  completion: PutawayCompletion;
  onAnother: () => void;
}

/**
 * What became of a confirmed put-away: recorded (and where), or saved on the phone for later. The
 * notes beneath it — a location that wasn't captured, a clock the server corrected — are said
 * plainly but never as errors, because the put-away itself went through.
 */
export function PutawayResult({ completion, onAnother }: PutawayResultProps) {
  const { summary, quantity } = completion;
  // The form that had focus has just been replaced by this; the next thing a worker does is start
  // another, so that is where focus goes (the result itself is announced by its status role).
  const moreRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    moreRef.current?.focus();
  }, []);
  const what = `${formatQty(quantity)} ${summary.uom} of ${summary.sku}`;
  const notes =
    completion.kind === "synced"
      ? completion.result.warnings.map(warningNotice).filter((note): note is string => note !== null)
      : [];

  return (
    <Card className="flex flex-col gap-4 p-4">
      {completion.kind === "synced" ? (
        <InlineAlert
          tone="success"
          title={completion.result.replayed ? "Already recorded" : "Put away recorded"}
        >
          {`${what} is in ${completion.result.to_location.code}${
            completion.result.to_location.name ? ` (${completion.result.to_location.name})` : ""
          }.`}
        </InlineAlert>
      ) : (
        <InlineAlert tone="info" title="Saved on this phone">
          {`${what} to ${summary.toBarcode}. ${queuedNotice(completion.reason)}`}
        </InlineAlert>
      )}

      {completion.kind === "synced" && completion.result.matched_suggestion === false ? (
        <p className="text-sm text-muted">That was not the suggested bin.</p>
      ) : null}

      {notes.map((note) => (
        <InlineAlert key={note} tone="warning" title={note} />
      ))}
      {completion.geoNotice ? <InlineAlert tone="info" title={completion.geoNotice} /> : null}

      <Button ref={moreRef} onClick={onAnother}>
        Put away more
      </Button>
    </Card>
  );
}
