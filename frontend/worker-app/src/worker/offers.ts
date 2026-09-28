import type { AssignmentOfferOut, PickTaskOut } from "@/api/types";

/** This worker's own live offer on a task — `ACCEPTED` (theirs to work), or `OFFERED` and not yet
 *  expired (theirs to answer). `null` for every other case, including an `OFFERED` offer whose
 *  `expires_at` has already passed: the sweep may not have flipped it to `EXPIRED` yet, but it is
 *  not live to *this* client, and accepting it would only earn the 409 ADR-0025 §3 promises. */
export function findMyLiveOffer(
  task: PickTaskOut,
  workerId: string,
  now: Date = new Date(),
): AssignmentOfferOut | null {
  return (
    task.offers.find((offer) => {
      if (offer.worker_id !== workerId) return false;
      if (offer.state === "ACCEPTED") return true;
      if (offer.state === "OFFERED") return new Date(offer.expires_at) > now;
      return false;
    }) ?? null
  );
}
