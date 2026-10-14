import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  PackageIcon,
  PageHeader,
  Select,
  Skeleton,
  StickyActionBar,
  TextField,
} from "@opsgrids/ui";

import { FULL_PAGE, confirmPackage, listPackages, listPackagingMaterials, listTasks, resolveLocationByBarcode } from "@/api/client";
import type { PackageLineIn, PackageOut, PackagingMaterialOut, PickTaskLineOut } from "@/api/types";
import { RequireGrant } from "@/auth/RequireGrant";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";
import { itemTitle, locationCode, orderLabel, qtyWithUnit } from "@/lib/display";
import { shortId } from "@/lib/shortId";
import { PackageStatusPill } from "@/ui/StatusPill";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface PackableLine {
  line: PickTaskLineOut;
  availableMilli: number;
}

export function PackPage() {
  return (
    <RequireGrant permission="operations.fulfilment.pack.read" scope="ORG" what="packing">
      <PackContent />
    </RequireGrant>
  );
}

function PackContent() {
  const { orderId } = useParams<{ orderId: string }>();
  const state = useAsync(async () => {
    const [tasks, packages, materials] = await Promise.all([
      listTasks({ order_id: orderId, size: FULL_PAGE }),
      listPackages({ order_id: orderId }),
      listPackagingMaterials(),
    ]);
    return { tasks: tasks.items, packages: packages.items, materials: materials.items };
  }, [orderId]);

  if (!orderId) return null;

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-9 w-40 rounded-tile" />
        <Skeleton className="h-72 w-full rounded-tile" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Pack order" />
        <Card className="p-2">
          <ErrorState title="Couldn't load this order's packing state" message={problemMessage(state.error)} onRetry={state.reload} />
        </Card>
      </div>
    );
  }

  const { tasks, packages, materials } = state.data;

  const packagedByLine = new Map<string, number>();
  for (const pkg of packages) {
    for (const line of pkg.lines) {
      packagedByLine.set(line.pick_task_line_id, (packagedByLine.get(line.pick_task_line_id) ?? 0) + qtyToMilli(line.quantity));
    }
  }

  const packable: PackableLine[] = [];
  for (const task of tasks) {
    for (const line of task.lines) {
      const picked = qtyToMilli(line.picked);
      if (picked <= 0) continue;
      const availableMilli = picked - (packagedByLine.get(line.id) ?? 0);
      if (availableMilli > 0) packable.push({ line, availableMilli });
    }
  }

  const anyPicked = tasks.some((task) => task.lines.some((line) => qtyToMilli(line.picked) > 0));

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        title="Pack order"
        description={`Order ${tasks[0] ? orderLabel(tasks[0]) : shortId(orderId)}`}
      />

      {!anyPicked ? (
        <Card className="p-2">
          <EmptyState
            icon={<PackageIcon className="size-6" aria-hidden />}
            title="Nothing staged to pack yet"
            description="Once a pick is confirmed for this order, its lines will appear here."
          />
        </Card>
      ) : packable.length === 0 ? (
        <InlineAlert tone="success" title="Packed — ready for handover">
          Every picked line for this order has been packed. A dispatcher takes it from here.
        </InlineAlert>
      ) : (
        <PackForm orderId={orderId} packable={packable} materials={materials} onPacked={state.reload} />
      )}

      {packages.length > 0 ? (
        <Card className="flex flex-col gap-3 p-4">
          <h2 className="text-sm font-semibold text-muted">Packages so far</h2>
          <ul className="flex flex-col gap-3">
            {packages.map((pkg) => (
              <PackageRow key={pkg.id} pkg={pkg} />
            ))}
          </ul>
        </Card>
      ) : null}

      <Link to="/" className="text-sm font-semibold text-accent hover:underline">
        Back to tasks
      </Link>
    </div>
  );
}

function PackageRow({ pkg }: { pkg: PackageOut }) {
  return (
    <li className="flex flex-col gap-1 rounded-tile bg-ground p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted">{shortId(pkg.id)}</span>
        <PackageStatusPill status={pkg.status} />
      </div>
      <ul className="flex flex-col gap-1">
        {pkg.lines.map((line) => (
          <li key={line.id} className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">{line.item_name ?? <span className="font-mono">{line.sku}</span>}</span>
            <span className="tnum shrink-0 text-muted">{qtyWithUnit(line.quantity, line.item_unit)}</span>
          </li>
        ))}
      </ul>
    </li>
  );
}

function PackForm({
  orderId,
  packable,
  materials,
  onPacked,
}: {
  orderId: string;
  packable: PackableLine[];
  materials: PackagingMaterialOut[];
  onPacked: () => void;
}) {
  const [destinationInput, setDestinationInput] = useState("");
  const [destinationId, setDestinationId] = useState<string | null>(null);
  const [destinationLabel, setDestinationLabel] = useState<string | null>(null);
  const [destinationWarning, setDestinationWarning] = useState<string | null>(null);
  const [destinationError, setDestinationError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const [materialId, setMaterialId] = useState<string>("");
  const [weightGrams, setWeightGrams] = useState("");
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(packable.map(({ line, availableMilli }) => [line.id, milliToQty(availableMilli)])),
  );
  const [lineErrors, setLineErrors] = useState<Record<string, string>>({});

  const pack = useMutation(
    (lines: PackageLineIn[]) =>
      confirmPackage({
        order_id: orderId,
        destination_location_id: destinationId ?? "",
        ...(materialId ? { packaging_material_id: materialId } : {}),
        ...(weightGrams.trim() ? { weight_grams: Number(weightGrams.trim()) } : {}),
        lines,
      }),
    {
      onSuccess: () => {
        setLineErrors({});
        onPacked();
      },
    },
  );

  async function verifyDestination() {
    const value = destinationInput.trim();
    if (!value) return;
    setDestinationError(null);
    setDestinationWarning(null);
    setResolving(true);
    try {
      const location = await resolveLocationByBarcode(value);
      setDestinationId(location.id);
      setDestinationLabel(locationCode(location.code, location.id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && UUID_RE.test(value)) {
        setDestinationId(value);
        setDestinationLabel(shortId(value));
        setDestinationWarning("Couldn't verify this location (missing permission) — using it as entered.");
      } else if (err instanceof ApiError && err.status === 404) {
        setDestinationError("We don't recognize that location barcode.");
      } else if (err instanceof ApiError && err.status === 403) {
        setDestinationError("Location lookup isn't available to your role — ask a dispatcher for the destination's id.");
      } else {
        setDestinationError(problemMessage(err));
      }
    } finally {
      setResolving(false);
    }
  }

  function submit() {
    if (!destinationId) {
      setDestinationError("Verify a destination location first.");
      return;
    }
    if (weightGrams.trim() !== "" && (!Number.isFinite(Number(weightGrams.trim())) || Number(weightGrams.trim()) < 0)) {
      setLineErrors({ _form: "Enter a valid weight in grams, or leave it blank." });
      return;
    }
    const errors: Record<string, string> = {};
    const lines: PackageLineIn[] = [];
    for (const { line, availableMilli } of packable) {
      const raw = (quantities[line.id] ?? "").trim();
      if (raw === "") continue;
      let milli: number;
      try {
        milli = qtyToMilli(raw);
      } catch {
        errors[line.id] = "Enter a valid quantity.";
        continue;
      }
      if (milli <= 0) continue;
      if (milli > availableMilli) {
        errors[line.id] = `Only ${formatQty(milliToQty(availableMilli))} is staged and unpacked.`;
        continue;
      }
      lines.push({ pick_task_line_id: line.id, quantity: raw });
    }
    if (Object.keys(errors).length > 0) {
      setLineErrors(errors);
      return;
    }
    if (lines.length === 0) {
      setLineErrors({ _form: "Enter a quantity for at least one line." });
      return;
    }
    setLineErrors({});
    void pack.mutate(lines);
  }

  return (
    <>
    <Card className="flex flex-col gap-4 p-4">
      <div className="flex flex-col gap-2">
        <div className="flex items-end gap-2">
          <TextField
            label="Destination location"
            helper="Scan the pack station or dock barcode, or enter its id."
            containerClassName="flex-1"
            value={destinationInput}
            onChange={(event) => setDestinationInput(event.target.value)}
          />
          <Button variant="secondary" onClick={() => void verifyDestination()} loading={resolving} disabled={!destinationInput.trim()}>
            Verify
          </Button>
        </div>
        {destinationLabel ? <p className="text-sm font-medium text-positive">Destination: {destinationLabel}</p> : null}
        {destinationWarning ? <p className="text-xs text-late">{destinationWarning}</p> : null}
        {destinationError ? <p className="text-xs font-medium text-negative">{destinationError}</p> : null}
      </div>

      {materials.length > 0 ? (
        <Select
          label="Packaging material"
          placeholder="None"
          value={materialId}
          onChange={(event) => setMaterialId(event.target.value)}
          options={materials.map((material) => ({ value: material.id, label: `${material.name} (${material.code})` }))}
        />
      ) : null}

      <TextField
        label="Weight (grams)"
        optional
        inputMode="numeric"
        value={weightGrams}
        onChange={(event) => setWeightGrams(event.target.value)}
      />

      <div className="flex flex-col gap-3">
        {packable.map(({ line, availableMilli }) => (
          <div key={line.id} className="flex items-end gap-2">
            <TextField
              label={itemTitle(line)}
              helper={`${qtyWithUnit(milliToQty(availableMilli), line.item_unit)} staged${line.item_name ? ` · ${line.sku}` : ""}`}
              containerClassName="flex-1"
              value={quantities[line.id] ?? ""}
              onChange={(event) => setQuantities((prev) => ({ ...prev, [line.id]: event.target.value }))}
              {...(lineErrors[line.id] !== undefined ? { error: lineErrors[line.id] } : {})}
            />
          </div>
        ))}
      </div>

      {lineErrors._form ? <InlineAlert tone="danger" title={lineErrors._form} /> : null}
      {pack.error ? <InlineAlert tone="danger" title="Couldn't confirm this package">{problemMessage(pack.error)}</InlineAlert> : null}

    </Card>

      <StickyActionBar always bleed={false}>
        <Button className="min-h-12 w-full" onClick={submit} loading={pack.pending} disabled={pack.pending}>
          Confirm package
        </Button>
      </StickyActionBar>
    </>
  );
}
