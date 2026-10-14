import { useCallback, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  BarChartIcon,
  Button,
  Card,
  InlineAlert,
  MapPinIcon,
  PageHeader,
  SampleChip,
  SearchIcon,
  Skeleton,
  TruckIcon,
  cn,
  useScrollFade,
} from "@opsgrids/ui";

import { getStoreSettings, listZones, USE_FIXTURES } from "./api";
import { DemandReport } from "./DemandReport";
import { DeliveryProblem } from "./DeliveryProblem";
import { allows, useDeliveryAccess } from "./permissions";
import { PostalCodeImport } from "./PostalCodeImport";
import { PostalCodesDialog } from "./PostalCodesDialog";
import { ServiceabilityTester } from "./ServiceabilityTester";
import { ZoneFormDialog } from "./ZoneFormDialog";
import { ZoneList } from "./ZoneList";
import type { ZoneOut } from "./types";

const VIEWS = [
  { key: "zones", label: "Zones", icon: MapPinIcon },
  { key: "import", label: "Import", icon: TruckIcon },
  { key: "tester", label: "Tester", icon: SearchIcon },
  { key: "demand", label: "Demand", icon: BarChartIcon },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

function viewFrom(value: string | null): ViewKey {
  const found = VIEWS.find((view) => view.key === value);
  return found ? found.key : "zones";
}

/**
 * The merchant's delivery admin — ADR-0010's slice 1, in four surfaces.
 *
 * The four are one route rather than four, because they are one job: you map an area, you check
 * what you mapped, and you read what you have been turning away. They are addressable
 * (`/delivery?view=demand`) so a link into any of them works and the back button behaves, and they
 * are links rather than a tab widget — a set of destinations is navigation, and navigation is
 * `<a>` elements.
 *
 * **Nothing here protects anything.** Every write is guarded server-side at organization scope and
 * every list endpoint injects the tenant into its query (ADR-0003 §3, ADR-0010 §9). The gating
 * below decides what is worth rendering, and the server decides what happens.
 */
export function DeliveryPage() {
  const [params] = useSearchParams();
  const view = viewFrom(params.get("view"));
  const access = useDeliveryAccess();

  const [attempt, setAttempt] = useState(0);
  const load = useCallback(async () => {
    const [store, zones] = await Promise.all([getStoreSettings(), listZones()]);
    return { store, zones };
  }, []);
  const state = useAsync(load, [attempt]);

  const [editing, setEditing] = useState<{ zone: ZoneOut | null } | null>(null);
  const [codesFor, setCodesFor] = useState<ZoneOut | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const viewsRef = useRef<HTMLUListElement>(null);
  const viewsFade = useScrollFade(viewsRef);

  const canWrite = allows(access.write);

  function refresh(message: string) {
    setAnnouncement(message);
    setEditing(null);
    setCodesFor(null);
    setAttempt((current) => current + 1);
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Delivery"
        description="Which postal codes you deliver to, what buyers are told when they ask, and which places you have been turning away."
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {USE_FIXTURES ? (
              <SampleChip title="Representative data — running on local fixtures: zones, checks and demand are sample data" />
            ) : null}
            {canWrite && state.status === "success" ? (
              <Button onClick={() => setEditing({ zone: null })}>Add a zone</Button>
            ) : null}
          </div>
        }
      />

      {/* Changes nobody clicked into view — a zone saved, an import applied — are heard without
          stealing focus from wherever the merchant is. */}
      <p aria-live="polite" role="status" className="sr-only">
        {announcement}
      </p>

      {access.entitled === false ? (
        <InlineAlert tone="warning" live="off" title="Storefront isn't on this organization's plan">
          This screen will load, because the browser is not where that is decided — but the server
          will refuse every call on it until an owner adds the product.
        </InlineAlert>
      ) : null}

      <nav aria-label="Delivery sections">
        {/* One row that scrolls sideways when the four do not fit, with the design system's edge
            fade saying there is more — the earlier version scrolled with no sign of it and clipped
            "Demand" at the edge, which is the failure the fade exists to prevent. */}
        <ul
          ref={viewsRef}
          data-fade={viewsFade}
          className="scroll-fade m-0 flex list-none gap-2 overflow-x-auto p-0"
        >
          {VIEWS.map((entry) => {
            const active = entry.key === view;
            const Icon = entry.icon;
            return (
              <li key={entry.key} className="shrink-0">
                {/* An `<a>`, not a button that navigates: these are four addresses, so they open
                    in a new tab, go back, and can be linked to. */}
                <Link
                  to={{ search: `?view=${entry.key}` }}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "inline-flex min-h-11 items-center gap-2 whitespace-nowrap rounded-full px-4 text-sm font-semibold transition-colors",
                    active ? "bg-accent text-white" : "text-muted hover:bg-ground hover:text-ink",
                  )}
                >
                  <Icon className="size-4" aria-hidden />
                  {entry.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {state.status === "loading" ? (
        /* The same height the loaded view occupies, so the page lands once instead of twice. */
        <div className="flex flex-col gap-4">
          <Skeleton className="h-14 w-full rounded-card" />
          <Card className="p-4">
            <Skeleton className="h-64 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <DeliveryProblem
            error={state.error}
            subject="Your delivery zones"
            onRetry={() => setAttempt((current) => current + 1)}
          />
        </Card>
      ) : null}

      {state.status === "success" ? (
        <>
          {state.data.store.status !== "published" ? (
            <InlineAlert
              tone="info"
              live="off"
              title={`This store is ${state.data.store.status}, so no buyer can check yet`}
            >
              The public serviceability route answers 404 until the store is published. Everything
              on this screen works now — map your zones and test them before anyone can ask.
            </InlineAlert>
          ) : null}

          {access.read === "denied" ? (
            <Card className="p-6">
              <InlineAlert tone="warning" title="You can't see delivery zones">
                This needs storefront.delivery.zone.read at organization scope. An owner or a
                storefront admin can grant it.
              </InlineAlert>
            </Card>
          ) : (
            <>
              {view === "zones" ? (
                <ZoneList
                  zones={state.data.zones}
                  store={state.data.store}
                  canWrite={canWrite}
                  onEdit={(zone) => setEditing({ zone })}
                  onPostalCodes={(zone) => setCodesFor(zone)}
                  onCreate={() => setEditing({ zone: null })}
                  onChanged={refresh}
                />
              ) : null}

              {view === "import" ? (
                canWrite ? (
                  <PostalCodeImport zones={state.data.zones} onImported={refresh} />
                ) : (
                  <Card className="p-6">
                    <InlineAlert tone="warning" title="You can't change the postal-code map">
                      Importing needs storefront.delivery.zone.write at organization scope. You can
                      still read the zones, test a postal code and see the demand report.
                    </InlineAlert>
                  </Card>
                )
              ) : null}

              {view === "tester" ? <ServiceabilityTester /> : null}
              {view === "demand" ? <DemandReport store={state.data.store} /> : null}
            </>
          )}

          {editing !== null ? (
            <ZoneFormDialog
              open
              zone={editing.zone}
              store={state.data.store}
              onClose={() => setEditing(null)}
              onSaved={(_zone, message) => refresh(message)}
            />
          ) : null}

          {codesFor !== null ? (
            <PostalCodesDialog
              open
              zone={codesFor}
              zones={state.data.zones}
              onClose={() => setCodesFor(null)}
              onSaved={refresh}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
