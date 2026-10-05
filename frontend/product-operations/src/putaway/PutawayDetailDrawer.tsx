import { Suspense, lazy, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { listWorkers } from "@/api/client";
import { getPutawayDetail, revealPutawayLocation } from "@/api/tracking";
import type { PutawayDetailOut, PutawayRevealOut } from "@/api/trackingTypes";
import { LOCATION_KIND_LABEL } from "@/data/locationKinds";
import { reportOnce } from "@/lib/clientReport";
import { formatDateTime } from "@/lib/format";
import { formatCoordinates, isValidMicroPair, microToDegrees, positionOf } from "@/lib/geo";
import { getMapsApiKey } from "@/lib/mapsConfig";
import { ApiError, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { Button, Drawer, EmptyState, ErrorState, InlineAlert, Skeleton } from "@/ui";
import { PanelBoundary } from "@/ui/PanelBoundary";
import { useSlowLoad } from "@/lib/useSlowLoad";
import { SlowLoadNotice } from "@/ui/SlowLoadNotice";
import { EyeIcon, EyeOffIcon, MapPinIcon } from "@opsgrids/ui";

const LocationPinMap = lazy(() => import("@/maps/LocationPinMap"));

interface PutawayDetailDrawerProps {
  /** The put-away's stock move, or `null` for closed. */
  moveId: string | null;
  onClose: () => void;
}

/**
 * One storage put-away, read from its ledger row: which bin the stock went into, who stored it,
 * when, and whether it matched the bin the system suggested.
 *
 * The worker's GPS fix is not on this screen. It sits behind "Show worker location", offered only
 * to a caller holding `operations.inventory.putaway.location_read` (the administrator role), and
 * that button POSTs: every successful reveal writes an audit row on the server, so nothing here can
 * read the position quietly, cache it or put it in an address. What comes back lives in this
 * drawer's state and goes when the drawer closes.
 */
export function PutawayDetailDrawer({ moveId, onClose }: PutawayDetailDrawerProps) {
  return (
    <Drawer open={moveId !== null} onClose={onClose} title="Put-away details" description="Where the stock was stored, and by whom.">
      {moveId !== null ? (
        <PanelBoundary title="The put-away details">
          <PutawayBody key={moveId} moveId={moveId} />
        </PanelBoundary>
      ) : null}
    </Drawer>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-3 text-sm first:border-t-0 first:pt-0">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right font-semibold text-ink">{children}</dd>
    </div>
  );
}

function PutawayBody({ moveId }: { moveId: string }) {
  const detail = useAsync(() => getPutawayDetail(moveId), [moveId]);
  const slow = useSlowLoad(detail.status === "loading");

  if (detail.status === "loading") {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        <Skeleton className="h-10 w-full rounded-tile" />
        <Skeleton className="h-10 w-full rounded-tile" />
        <Skeleton className="h-10 w-full rounded-tile" />
        <SlowLoadNotice show={slow} what="the put-away" />
      </div>
    );
  }
  if (detail.status === "error") {
    if (detail.error instanceof ApiError && detail.error.status === 404) {
      return (
        <EmptyState
          icon={<MapPinIcon className="size-6" />}
          title="No capture on record"
          description="This put-away has no recorded capture. Captures are kept for 30 days, so an older one has expired and was removed."
        />
      );
    }
    return <ErrorState title="Couldn't load this put-away" message={problemMessage(detail.error)} onRetry={detail.reload} />;
  }
  return <PutawayFacts moveId={moveId} detail={detail.data} />;
}

function PutawayFacts({ moveId, detail }: { moveId: string; detail: PutawayDetailOut }) {
  const workerName = useWorkerName(detail.worker_id);
  const canReveal = useCan("operations.inventory.putaway.location_read", "ORG");
  return (
    <div className="flex flex-col gap-6">
      <dl className="flex flex-col">
        <Row label="Stored in">
          <span className="tnum">{detail.to_location.code}</span> · {detail.to_location.name}{" "}
          <span className="font-normal text-muted">({LOCATION_KIND_LABEL[detail.to_location.kind]})</span>
        </Row>
        <Row label="Stored by">{detail.worker_id === null ? "A manager (no worker profile)" : workerName}</Row>
        <Row label="Captured">
          <span className="tnum">{formatDateTime(detail.captured_at)}</span>
        </Row>
        <Row label="Matched the suggestion">
          {detail.matched_suggestion === null ? "No bin was suggested" : detail.matched_suggestion ? "Yes" : "No"}
        </Row>
        <Row label="Position captured">{detail.has_location ? "Yes" : "No"}</Row>
      </dl>

      {canReveal ? <RevealSection moveId={moveId} hasLocation={detail.has_location} /> : null}
    </div>
  );
}

/** The worker's name when the caller may read workers; a generic phrase otherwise. Never an id. */
function useWorkerName(workerId: string | null): string {
  const canRead = useCan("operations.fulfilment.worker.read", "ORG");
  const workers = useAsync(
    () => (canRead && workerId !== null ? listWorkers({ size: 200 }) : Promise.resolve(null)),
    [canRead, workerId],
  );
  if (workers.status !== "success" || workers.data === null) return "A warehouse worker";
  return workers.data.items.find((w) => w.id === workerId)?.display_name || "A warehouse worker";
}

/**
 * The reveal route needs a session that signed in with a one-time code: the server answers a
 * password-only or single-sign-on session with a 403 whose detail is `Additional authentication
 * required: otp` (`require(..., amr={"otp"})` in `platform_core/auth.py`). That problem's `type` is the
 * generic `forbidden`, so the detail is the only thing that tells it apart from a plain missing
 * permission; every other 403 keeps the server's own sentence.
 */
function needsOneTimeCodeSession(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 403) return false;
  const detail = error.problem?.detail ?? "";
  return detail.startsWith("Additional authentication required:") && /\botp\b/.test(detail);
}

const ONE_TIME_CODE_MESSAGE =
  "Revealing a worker's location needs a session signed in with a one-time code. Sign in again with your authenticator code, or ask an admin who has MFA enabled.";

function RevealSection({ moveId, hasLocation }: { moveId: string; hasLocation: boolean }) {
  const [revealed, setRevealed] = useState<PutawayRevealOut | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const reveal = useMutation<void, PutawayRevealOut>(() => revealPutawayLocation(moveId), {
    onSuccess: (result) => {
      setRevealed(result);
      const position = positionOf(result);
      setAnnouncement(
        position
          ? `Worker location shown: ${formatCoordinates(position.lat_micro, position.lng_micro, result.accuracy_m)}.`
          : "Worker location shown: no usable position was recorded.",
      );
    },
  });

  // One button toggles Show and Hide, so it is never removed from under the keyboard. It is
  // disabled while the request is in flight, and a browser takes focus off a control that becomes
  // disabled, so focus is put back on it when the request settles, whether it succeeded or failed.
  const toggleRef = useRef<HTMLButtonElement>(null);
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !reveal.pending) toggleRef.current?.focus();
    wasPending.current = reveal.pending;
  }, [reveal.pending]);

  if (!hasLocation) {
    return (
      <InlineAlert tone="info" title="No position was captured" live="off">
        The device did not report a position for this put-away, so there is nothing to show.
      </InlineAlert>
    );
  }

  const error = reveal.error;
  const expired = error instanceof ApiError && error.status === 404;
  const needsCode = needsOneTimeCodeSession(error);
  return (
    <section aria-labelledby="reveal-heading" className="flex flex-col gap-3">
      <h3 id="reveal-heading" className="text-sm font-bold text-ink">
        Worker location
      </h3>
      {revealed === null ? (
        <p className="text-sm text-muted">
          This is personal location data. Showing it is recorded in the audit log, every time.
        </p>
      ) : (
        <RevealedPosition revealed={revealed} />
      )}
      <Button
        ref={toggleRef}
        variant="secondary"
        size={revealed === null ? "md" : "sm"}
        leadingIcon={revealed === null ? <EyeIcon className="size-4" /> : <EyeOffIcon className="size-4" />}
        loading={reveal.pending}
        onClick={() => {
          if (revealed === null) {
            void reveal.mutate();
          } else {
            setRevealed(null);
            setAnnouncement("Worker location hidden.");
          }
        }}
        className="w-fit"
      >
        {revealed === null ? "Show worker location" : "Hide location"}
      </Button>
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      {error ? (
        <InlineAlert
          tone="danger"
          title={expired ? "Capture expired" : needsCode ? "A one-time-code sign-in is needed" : "Couldn't show the location"}
        >
          {expired
            ? "This capture is older than its 30-day retention window and has been removed."
            : needsCode
              ? ONE_TIME_CODE_MESSAGE
              : problemMessage(error)}
        </InlineAlert>
      ) : null}
    </section>
  );
}

function RevealedPosition({ revealed }: { revealed: PutawayRevealOut }) {
  const { lat_micro: lat, lng_micro: lng, accuracy_m: accuracy } = revealed;
  const [mapFailed, setMapFailed] = useState(false);
  const apiKey = getMapsApiKey();
  const hasPosition = lat !== null && lng !== null && isValidMicroPair(lat, lng);
  if (!hasPosition) {
    return (
      <InlineAlert tone="info" title="No position was captured" live="off">
        The record holds no usable position for this put-away.
      </InlineAlert>
    );
  }

  const showMap = apiKey !== null && !mapFailed;
  return (
    <div className="flex flex-col gap-3">
      {showMap ? (
        <PanelBoundary
          title="The map"
          onFail={() => {
            reportOnce("maps_load_failed", "script_error");
            setMapFailed(true);
          }}
          fallback={() => null}
        >
          <Suspense fallback={<MapLoading />}>
            <LocationPinMap
              apiKey={apiKey}
              lat={microToDegrees(lat)}
              lng={microToDegrees(lng)}
              accuracyM={accuracy}
              onFailure={(reason) => {
                reportOnce("maps_load_failed", reason);
                setMapFailed(true);
              }}
            />
          </Suspense>
        </PanelBoundary>
      ) : null}
      <p className="tnum text-sm font-semibold text-ink">
        <span className="sr-only">Reported position: </span>
        {formatCoordinates(lat, lng, accuracy)}
      </p>
      <p className="text-xs text-muted">
        {showMap
          ? "The circle is the device's reported accuracy. The bin the stock was scanned into is the location of record."
          : "The map isn't available, so the position is shown as coordinates. The bin the stock was scanned into is the location of record."}
      </p>
    </div>
  );
}

function MapLoading() {
  const slow = useSlowLoad(true);
  return (
    <div className="flex flex-col gap-2" aria-busy="true">
      <div className="flex h-64 w-full items-center justify-center rounded-tile bg-sunken">
        <p className="text-sm text-muted">Loading the map…</p>
      </div>
      <SlowLoadNotice show={slow} what="the map" />
    </div>
  );
}
