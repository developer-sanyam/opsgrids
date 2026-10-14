import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { listCountLines, submitCount } from "@/api/client";
import { ScanCamera } from "@/ui/ScanCamera";
import { ScanInput } from "@/ui/ScanInput";
import { problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  ArrowRightIcon,
  Button,
  Card,
  ErrorState,
  InlineAlert,
  PageHeader,
  Skeleton,
  StickyActionBar,
  TextField,
  useToast,
} from "@opsgrids/ui";

function BackLink({ sessionId }: { sessionId: string }) {
  return (
    <Link to={`/counts/${sessionId}`} className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted">
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to lines
    </Link>
  );
}

/**
 * Count one line (ADR-0026, Fulfilment plan F8): scan the location's barcode to confirm the right
 * bin, then enter what is physically there — scan or manual, either fills `counted`. A count is
 * submitted once; there is no edit here afterward (`CountLineSubmitIn`'s own docstring) — a
 * mistake is caught by the manager's review, not re-typed on this screen.
 */
export function CountLineEntryPage() {
  const { sessionId = "", lineId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [barcode, setBarcode] = useState("");
  const [quantity, setQuantity] = useState("");
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);

  // No single-line GET exists — the list already loaded on the lines screen is the source; a
  // fresh fetch here is one extra request rather than plumbing router state through, and this
  // screen is reached rarely enough (once per line) that the cost is not worth avoiding.
  const linesState = useAsync(() => listCountLines(sessionId), [sessionId]);
  const line = linesState.status === "success" ? linesState.data.items.find((l) => l.id === lineId) : undefined;

  const submit = useMutation(
    (input: { counted: string; count_method: "scan" | "manual"; scanned_barcode?: string }) =>
      submitCount(sessionId, lineId, input),
    {
      onSuccess: () => {
        toast.success("Count submitted.");
        navigate(`/counts/${sessionId}`);
      },
    },
  );

  function handleSubmit() {
    setQuantityError(undefined);
    if (quantity.trim().length === 0) {
      setQuantityError("Enter what you counted.");
      return;
    }
    void submit.mutate({
      counted: quantity.trim(),
      count_method: barcode.trim() ? "scan" : "manual",
      ...(barcode.trim() ? { scanned_barcode: barcode.trim() } : {}),
    });
  }

  if (linesState.status === "loading") {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink sessionId={sessionId} />
        <Skeleton className="h-48 w-full rounded-tile" />
      </div>
    );
  }

  if (linesState.status === "error") {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink sessionId={sessionId} />
        <Card className="p-2">
          <ErrorState title="Couldn't load this line" message={problemMessage(linesState.error)} onRetry={linesState.reload} />
        </Card>
      </div>
    );
  }

  if (!line) {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink sessionId={sessionId} />
        <InlineAlert tone="danger" title="That line could not be found in this session." />
      </div>
    );
  }

  if (line.counted !== null) {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink sessionId={sessionId} />
        <InlineAlert tone="info" title="Already counted">{`This line was counted as ${line.counted}.`}</InlineAlert>
      </div>
    );
  }

  return (
    <div className="rise flex flex-col gap-4">
      <BackLink sessionId={sessionId} />
      <PageHeader title={line.sku || "Count this item"} description={`Expected ${line.expected}`} />

      {submit.error ? <InlineAlert tone="danger" title={problemMessage(submit.error)} /> : null}

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <ScanCamera
          onDetected={(value) => setBarcode(value.trim())}
          subject="the bin's label"
          videoLabel="Camera view for scanning the location barcode"
        />
        <ScanInput
          label="Location barcode"
          value={barcode}
          onChange={setBarcode}
          placeholder="Scan the bin label to confirm you're at the right spot"
          helper="Optional, but recommended before counting."
        />
        <TextField
          label="Counted quantity"
          inputMode="decimal"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          className="text-2xl"
          placeholder="0"
          required
          {...(quantityError !== undefined ? { error: quantityError } : {})}
        />
      </Card>

      <StickyActionBar always bleed={false}>
        <Button className="min-h-12 w-full" loading={submit.pending} onClick={handleSubmit}>
          Submit count
        </Button>
      </StickyActionBar>
    </div>
  );
}
