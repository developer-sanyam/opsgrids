import { useEffect, useState } from "react";

import { getOrganizationPricing } from "@/api/client";
import type { OrganizationPricing } from "@/api/types";

export type Live =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "ready"; data: OrganizationPricing; refreshing: boolean };

/**
 * The live pricing composite, **kept on screen while it reloads**. The page-level `useAsync`
 * replaces its data with a skeleton on every reload, which unmounts whatever stands on it — a
 * dialog and the control that opened it included. The staff action cards (Change plan, Extend
 * trial) reload quietly instead, so the button the operator pressed is still the button on the page
 * when the dialog closes, and focus has somewhere to return to.
 */
export function useLivePricing(organizationId: string, reloadKey: number): Live {
  const [live, setLive] = useState<Live>({ status: "loading" });

  useEffect(() => {
    let active = true;
    setLive((previous) =>
      previous.status === "ready" ? { ...previous, refreshing: true } : { status: "loading" },
    );
    getOrganizationPricing(organizationId)
      .then((data) => {
        if (active) setLive({ status: "ready", data, refreshing: false });
      })
      .catch((err: unknown) => {
        if (!active) return;
        setLive((previous) =>
          previous.status === "ready"
            ? { ...previous, refreshing: false }
            : { status: "error", error: err instanceof Error ? err : new Error(String(err)) },
        );
      });
    return () => {
      active = false;
    };
  }, [organizationId, reloadKey]);

  return live;
}
