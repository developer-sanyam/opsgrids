import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  discardBomVersion,
  getBom,
  getBomVersion,
  publishBomVersion,
  removeBomComponent,
  removeBomSubstitute,
  retireBomVersion,
} from "@/api/client";
import type { BomComponentOut, BomSubstituteOut, BomVersionDetailOut } from "@/api/types";
import { BomComponentDialog } from "@/forms/BomComponentDialog";
import { BomSubstituteDialog } from "@/forms/BomSubstituteDialog";
import { BomVersionEditDialog } from "@/forms/BomVersionEditDialog";
import { BackLink } from "@/routes/manufacturing/BackLink";
import { ExplosionPanel } from "@/routes/manufacturing/ExplosionPanel";
import { formatDateRange, formatDateTime } from "@/lib/format";
import { formatPercent, formatQty } from "@/lib/quantity";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  BomVersionStatusPill,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  RowActions,
  SectionHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { PlusIcon, Tabs, useToast } from "@opsgrids/ui";

type Removal =
  | { kind: "component"; component: BomComponentOut }
  | { kind: "substitute"; component: BomComponentOut; substitute: BomSubstituteOut };

type Tab = "components" | "explosion";

/**
 * One BOM version: its batch size, dates and components. While it is a DRAFT and the user holds
 * `operations.manufacturing.bom.write` the recipe can be edited; `...bom.publish` publishes a draft
 * and retires a published version. A PUBLISHED or RETIRED version is shown read-only with its
 * content hash — it is immutable by design. Every control here is presentation; the server refuses
 * an edit to a frozen version and a call without the permission on its own.
 */
export function BomVersionDetailPage() {
  const { bomId = "", versionId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.manufacturing.bom.write", "ORG");
  const canPublish = useCan("operations.manufacturing.bom.publish", "ORG");
  const keyFor = useIdempotencyKeys();

  const bomState = useAsync(() => getBom(bomId), [bomId]);
  const versionState = useAsync(() => getBomVersion(bomId, versionId), [bomId, versionId]);
  const [version, setVersion] = useState<BomVersionDetailOut | null>(null);
  // `versionState` is a fresh object every render; its `data` is stable per load, so only a real
  // (re)load replaces what an edit has since put in local state.
  useEffect(() => {
    if (versionState.data) setVersion(versionState.data);
  }, [versionState.data]);

  const [tab, setTab] = useState<Tab>("components");
  const [editing, setEditing] = useState(false);
  const [componentDialog, setComponentDialog] = useState<{ component?: BomComponentOut } | null>(null);
  const [substituteFor, setSubstituteFor] = useState<BomComponentOut | null>(null);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [confirm, setConfirm] = useState<"publish" | "retire" | "discard" | null>(null);

  const publish = useMutation(
    () => publishBomVersion(bomId, versionId, { idempotencyKey: keyFor("publish") }),
    {
      onSuccess: (saved) => {
        setVersion(saved);
        setConfirm(null);
        toast.success(`Version ${saved.version_no} published.`);
      },
    },
  );
  const retire = useMutation(() => retireBomVersion(bomId, versionId), {
    onSuccess: (saved) => {
      setVersion(saved);
      setConfirm(null);
      toast.success(`Version ${saved.version_no} retired.`);
    },
  });
  const discard = useMutation(() => discardBomVersion(bomId, versionId), {
    onSuccess: () => {
      toast.success("Draft discarded.");
      navigate(`/manufacturing/boms/${bomId}`);
    },
  });
  const remove = useMutation(
    async (target: Removal) => {
      if (target.kind === "component") await removeBomComponent(bomId, versionId, target.component.id);
      else await removeBomSubstitute(bomId, versionId, target.component.id, target.substitute.id);
      return getBomVersion(bomId, versionId);
    },
    {
      onSuccess: (saved) => {
        setVersion(saved);
        setRemoval(null);
      },
    },
  );

  const back = <BackLink to={`/manufacturing/boms/${bomId}`}>Back to this BOM</BackLink>;

  if (versionState.status === "loading" || bomState.status === "loading" || (versionState.status === "success" && version === null)) {
    return (
      <div className="rise flex flex-col gap-6" aria-busy="true" aria-label="Loading version">
        {back}
        <Skeleton className="h-24 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (versionState.status === "error" || bomState.status === "error") {
    const error = versionState.error ?? bomState.error;
    return (
      <div className="rise flex flex-col gap-6">
        {back}
        <ErrorState
          title="Couldn't load this version"
          message={problemMessage(error)}
          onRetry={() => {
            versionState.reload();
            bomState.reload();
          }}
        />
      </div>
    );
  }
  if (version === null || bomState.status !== "success") return null;

  const bom = bomState.data;
  const draft = version.status === "DRAFT";
  const editable = draft && canWrite;
  const frozen = version.status !== "DRAFT";

  const columns: Column<BomComponentOut>[] = [
    { key: "line", header: "Line", render: (c) => <span className="tnum text-muted">{c.line_no}</span> },
    {
      key: "item",
      header: "Component",
      render: (c) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{c.sku}</span>
          <span className="text-xs text-muted">{c.name}</span>
          {c.notes ? <span className="text-xs text-muted">{c.notes}</span> : null}
        </div>
      ),
    },
    {
      key: "quantity",
      header: "Quantity",
      align: "right",
      render: (c) => (
        <span className="tnum">
          {formatQty(c.quantity)} {c.uom}
        </span>
      ),
    },
    {
      key: "scrap",
      header: "Scrap",
      align: "right",
      render: (c) => <span className="tnum text-muted">{formatPercent(c.scrap_percent)}</span>,
    },
    {
      key: "effective",
      header: "Effective",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (c) => <span className="text-muted">{formatDateRange(c.effective_from, c.effective_to)}</span>,
    },
    {
      key: "level",
      header: "Made in-house",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (c) => (c.is_produced ? <Tag tone="info">Produced</Tag> : <span className="text-muted">Purchased</span>),
    },
    {
      key: "substitutes",
      header: "Substitutes",
      render: (c) =>
        c.substitutes.length === 0 ? (
          <span className="text-muted">None</span>
        ) : (
          <ul className="flex flex-col gap-1">
            {c.substitutes.map((s) => (
              <li key={s.id} className="flex items-center gap-2 text-sm">
                <span className="font-semibold text-ink">{s.sku}</span>
                <span className="tnum text-muted">ratio {formatQty(s.ratio)}</span>
                {editable ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove substitute ${s.sku} from ${c.sku}`}
                    onClick={() => setRemoval({ kind: "substitute", component: c, substitute: s })}
                  >
                    Remove
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (c) =>
        editable ? (
          <RowActions
            label={`Component ${c.sku}`}
            actions={[
              { label: "Edit", onSelect: () => setComponentDialog({ component: c }) },
              { label: "Add substitute", onSelect: () => setSubstituteFor(c) },
              { label: "Remove", tone: "danger", onSelect: () => setRemoval({ kind: "component", component: c }) },
            ]}
          />
        ) : null,
    },
  ];

  const components = (
    <div className="flex flex-col gap-4 pt-4">
      <DataTable
        columns={columns}
        rows={version.components}
        rowKey={(c) => c.id}
        caption={`Components of version ${version.version_no}`}
        emptyState={
          <EmptyState
            icon={<PlusIcon className="size-6" />}
            title="No components yet"
            description={editable ? "Add the first component to define this recipe." : "This version has no components."}
            action={
              editable ? (
                <Button size="sm" onClick={() => setComponentDialog({})}>
                  Add component
                </Button>
              ) : undefined
            }
          />
        }
      />
    </div>
  );

  return (
    <div className="rise flex flex-col gap-6">
      {back}
      <PageHeader
        eyebrow={`${bom.code} · ${bom.name}`}
        title={`Version ${version.version_no}`}
        description={`Per ${formatQty(version.output_quantity)} ${bom.uom} of ${bom.sku}. Effective: ${formatDateRange(version.effective_from, version.effective_to)}.`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <BomVersionStatusPill status={version.status} />
            {editable ? (
              <>
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Edit details
                </Button>
                <Button variant="secondary" leadingIcon={<PlusIcon className="size-4" />} onClick={() => setComponentDialog({})}>
                  Add component
                </Button>
                <Button variant="destructive" onClick={() => setConfirm("discard")}>
                  Discard draft
                </Button>
              </>
            ) : null}
            {draft && canPublish ? <Button onClick={() => setConfirm("publish")}>Publish</Button> : null}
            {version.status === "PUBLISHED" && canPublish ? (
              <Button variant="destructive" onClick={() => setConfirm("retire")}>
                Retire
              </Button>
            ) : null}
          </div>
        }
      />

      {frozen ? (
        <InlineAlert tone="info" title="Immutable — start a new version to change it" live="off">
          <p>
            {version.status === "PUBLISHED" ? "Published" : "Retired"}
            {version.published_at ? ` ${formatDateTime(version.published_at)}` : ""}. Its content is fixed.
          </p>
          {version.content_hash ? (
            <p className="text-sm">
              Content hash: <span className="tnum break-all font-semibold">{version.content_hash}</span>
            </p>
          ) : null}
        </InlineAlert>
      ) : null}
      {version.notes ? <p className="max-w-2xl text-sm text-muted">{version.notes}</p> : null}

      <Card className="flex flex-col p-4 sm:p-6">
        <SectionHeader
          title="Recipe"
          subtitle={`${version.component_count} component${version.component_count === 1 ? "" : "s"}`}
        />
        <Tabs
          label="Version sections"
          value={tab}
          onChange={(id) => setTab(id as Tab)}
          items={[
            { id: "components", label: "Components" },
            { id: "explosion", label: "Explosion" },
          ]}
          className="mt-4"
        >
          {tab === "components" ? (
            components
          ) : (
            <div className="pt-4">
              <ExplosionPanel
                bomId={bomId}
                versionId={versionId}
                defaultQuantity={version.output_quantity}
                uom={bom.uom}
              />
            </div>
          )}
        </Tabs>
      </Card>

      {editing ? (
        <BomVersionEditDialog
          version={version}
          outputUnit={bom.uom}
          onClose={() => setEditing(false)}
          onSaved={(saved) => {
            setVersion(saved);
            setEditing(false);
          }}
        />
      ) : null}
      {componentDialog ? (
        <BomComponentDialog
          bomId={bomId}
          versionId={versionId}
          {...(componentDialog.component ? { component: componentDialog.component } : {})}
          onClose={() => setComponentDialog(null)}
          onSaved={(saved) => {
            setVersion(saved);
            setComponentDialog(null);
          }}
        />
      ) : null}
      {substituteFor ? (
        <BomSubstituteDialog
          bomId={bomId}
          versionId={versionId}
          component={substituteFor}
          onClose={() => setSubstituteFor(null)}
          onSaved={(saved) => {
            setVersion(saved);
            setSubstituteFor(null);
          }}
        />
      ) : null}

      <ConfirmDialog
        open={removal !== null}
        onClose={() => {
          setRemoval(null);
          remove.reset();
        }}
        onConfirm={() => removal && remove.mutate(removal)}
        title={removal?.kind === "substitute" ? "Remove this substitute?" : "Remove this component?"}
        body={
          removal?.kind === "substitute"
            ? `${removal.substitute.sku} will no longer be offered in place of ${removal.component.sku}.`
            : removal
              ? `${removal.component.sku} and its substitutes come out of this draft.`
              : ""
        }
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
      <ConfirmDialog
        open={confirm === "publish"}
        onClose={() => {
          setConfirm(null);
          publish.reset();
        }}
        onConfirm={() => publish.mutate(undefined)}
        title={`Publish version ${version.version_no}?`}
        body="Publishing makes this version immutable: its components, quantities and substitutes can never be edited again. To change the recipe later you start a new version."
        confirmLabel="Publish"
        pendingLabel="Publishing…"
        tone="primary"
        pending={publish.pending}
        {...(publish.error ? { error: problemMessage(publish.error) } : {})}
      />
      <ConfirmDialog
        open={confirm === "retire"}
        onClose={() => {
          setConfirm(null);
          retire.reset();
        }}
        onConfirm={() => retire.mutate(undefined)}
        title={`Retire version ${version.version_no}?`}
        body="No new production order can be planned from a retired version. Orders already planned from it are not affected."
        confirmLabel="Retire"
        pendingLabel="Retiring…"
        pending={retire.pending}
        {...(retire.error ? { error: problemMessage(retire.error) } : {})}
      />
      <ConfirmDialog
        open={confirm === "discard"}
        onClose={() => {
          setConfirm(null);
          discard.reset();
        }}
        onConfirm={() => discard.mutate(undefined)}
        title="Discard this draft?"
        body="The draft and everything in it is deleted. Published versions are not affected."
        confirmLabel="Discard draft"
        pendingLabel="Discarding…"
        pending={discard.pending}
        {...(discard.error ? { error: problemMessage(discard.error) } : {})}
      />
    </div>
  );
}
