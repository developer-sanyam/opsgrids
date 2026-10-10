import { useRef, useState } from "react";

import { exportData, getImportTemplate, runImport } from "@/api/scan";
import type { ExportEntity, ImportEntity, ImportResult, ImportRowError, OnDuplicate } from "@/api/scan";
import { DataFileError, downloadText, readImportFile, sampleCsv, toCsv } from "@/lib/dataFile";
import type { ReadRows } from "@/lib/dataFile";
import { useRequestKey } from "@/scan/useRequestKey";
import { ApiError, problemMessage, useAsync, useCan } from "@opsgrids/client";
import { Button, Card, EmptyState, ErrorState, InlineAlert, PageHeader, Select, Skeleton } from "@/ui";

const IMPORT_LABEL: Record<ImportEntity, string> = {
  items: "Items",
  locations: "Locations",
  lots: "Lots",
  rolls: "Rolls",
  opening_stock: "Opening stock",
};

/** The write permission each import also needs — the server checks it; the screen hides what
 *  would only be refused. */
const IMPORT_WRITE: Record<ImportEntity, string> = {
  items: "operations.inventory.item.write",
  locations: "operations.inventory.location.write",
  lots: "operations.inventory.lot.write",
  rolls: "operations.inventory.roll.write",
  opening_stock: "operations.inventory.stock.adjust",
};

const EXPORT_LABEL: Record<ExportEntity, string> = {
  items: "Items",
  locations: "Locations",
  lots: "Lots",
  rolls: "Rolls",
  stock: "Stock by bin",
  ledger: "Stock ledger",
};

const IMPORT_HINT: Record<ImportEntity, string> = {
  items: "Creates items. An item that already exists (same SKU) is never overwritten.",
  locations: "Creates zones, racks and bins. A parent may come earlier in the same file.",
  lots: "Registers lots of lot-tracked items.",
  rolls: "Registers rolls of roll-tracked items. Import lots first if rolls name them.",
  opening_stock:
    "Brings existing stock onto the books through the ledger, one empty bin at a time. A bin that already holds stock is refused, so a file can never double it.",
};

/** The row errors of a refused commit, from the problem document the server answers 422 with. */
function errorsFrom(error: unknown): ImportRowError[] | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const list = error.problem?.errors as
    | readonly { row?: number; field?: string | null; code: string; message: string }[]
    | undefined;
  if (!list || !list.some((e) => typeof e.row === "number")) return null;
  return list.map((e) => ({ row: e.row ?? 0, field: e.field ?? null, code: e.code, message: e.message }));
}

function ErrorTable({ errors, total }: { errors: ImportRowError[]; total: number }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="max-h-96 overflow-auto rounded-tile border border-line">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-sunken text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2">Row</th>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Problem</th>
            </tr>
          </thead>
          <tbody>
            {errors.map((e, i) => (
              <tr key={`${e.row}-${i}`} className="border-t border-line align-top">
                <td className="tnum px-3 py-2 font-semibold">{e.row}</td>
                <td className="px-3 py-2">{e.field ?? "—"}</td>
                <td className="px-3 py-2">{e.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > errors.length ? (
        <p className="text-sm text-muted">Showing the first {errors.length} of {total} problems.</p>
      ) : null}
    </div>
  );
}

/**
 * `/inventory/import` — bulk import and export of the inventory lists (inventory Phase 9,
 * ADR-0027). Import is **all or nothing** and runs on the server (ADR-0022's per-row browser
 * import cannot make one bad row stop the rest): choose what you are importing, drop a CSV or
 * XLSX, read the row-by-row preview, and commit only when it is clean. Every cell is sent as the
 * text in the file; the server validates it.
 */
export function InventoryImportPage() {
  const canImport = useCan("operations.inventory.data.import", "ORG");
  const canExport = useCan("operations.inventory.data.export", "ORG");
  const writable: Record<ImportEntity, boolean> = {
    items: useCan(IMPORT_WRITE.items, "ORG"),
    locations: useCan(IMPORT_WRITE.locations, "ORG"),
    lots: useCan(IMPORT_WRITE.lots, "ORG"),
    rolls: useCan(IMPORT_WRITE.rolls, "ORG"),
    opening_stock: useCan(IMPORT_WRITE.opening_stock, "ORG"),
  };
  const entities = (Object.keys(IMPORT_LABEL) as ImportEntity[]).filter((e) => writable[e]);

  const [entity, setEntity] = useState<ImportEntity>("items");
  const [onDuplicate, setOnDuplicate] = useState<OnDuplicate>("fail");
  const [file, setFile] = useState<{ name: string; read: ReadRows } | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [committed, setCommitted] = useState<ImportResult | null>(null);
  const [refused, setRefused] = useState<ImportRowError[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<"reading" | "previewing" | "committing" | null>(null);
  const [exporting, setExporting] = useState<ExportEntity | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const keyFor = useRequestKey();
  const inputRef = useRef<HTMLInputElement>(null);

  // No permission, no request: the columns are only fetched for someone who can import.
  const template = useAsync(
    () => (canImport ? getImportTemplate(entity) : Promise.resolve(null)),
    [entity, canImport],
  );
  const columns = template.status === "success" ? (template.data?.columns ?? null) : null;

  const clear = () => {
    setFile(null);
    setPreview(null);
    setCommitted(null);
    setRefused(null);
    setMessage(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const check = async (name: string, read: ReadRows, duplicate: OnDuplicate) => {
    setBusy("previewing");
    setMessage(null);
    setRefused(null);
    try {
      setPreview(
        await runImport({ entity, mode: "preview", onDuplicate: duplicate, rows: read.rows, sourceName: name }),
      );
    } catch (error) {
      setPreview(null);
      setMessage(problemMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const choose = async (picked: File | undefined) => {
    if (!picked || !columns) return;
    clear();
    setBusy("reading");
    try {
      const read = await readImportFile(picked, columns);
      if (read.missingRequired.length > 0) {
        setMessage(`The file has no ${read.missingRequired.join(", ")} column. Download the sample for the right headers.`);
        setBusy(null);
        return;
      }
      setFile({ name: picked.name, read });
      await check(picked.name, read, onDuplicate);
    } catch (error) {
      setMessage(error instanceof DataFileError ? error.message : problemMessage(error));
      setBusy(null);
    }
  };

  const commit = async () => {
    if (!file || !preview || preview.error_count > 0) return;
    setBusy("committing");
    setMessage(null);
    const request = { entity, mode: "commit" as const, onDuplicate, rows: file.read.rows, sourceName: file.name };
    try {
      const result = await runImport(request, keyFor({ entity, onDuplicate, rows: file.read.rows }));
      setCommitted(result);
      setPreview(null);
    } catch (error) {
      const rows = errorsFrom(error);
      if (rows) setRefused(rows);
      else setMessage(problemMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const download = async (target: ExportEntity) => {
    setExporting(target);
    setExportNote(null);
    try {
      const out = await exportData(target);
      downloadText(`inventory-${target}.csv`, toCsv(out.columns, out.rows));
      setExportNote(
        out.truncated
          ? `${EXPORT_LABEL[target]}: the list is longer than one export holds; this file has the first ${out.rows.length} rows.`
          : `${EXPORT_LABEL[target]}: ${out.rows.length} row${out.rows.length === 1 ? "" : "s"} downloaded.`,
      );
    } catch (error) {
      setExportNote(problemMessage(error));
    } finally {
      setExporting(null);
    }
  };

  const downloadSample = () => {
    if (!columns) return;
    downloadText(`inventory-${entity}-sample.csv`, sampleCsv(columns));
  };

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <PageHeader
        title="Import and export"
        description="Bring inventory in from a spreadsheet — all rows or none — and take any list out."
      />

      {canImport && entities.length > 0 ? (
        <Card className="flex flex-col gap-4 p-6">
          <h2 className="text-lg font-semibold">Import</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              label="What are you importing?"
              value={entity}
              onChange={(e) => {
                setEntity(e.target.value as ImportEntity);
                clear();
              }}
              options={entities.map((e) => ({ value: e, label: IMPORT_LABEL[e] }))}
            />
            <Select
              label="A record that already exists"
              value={onDuplicate}
              onChange={(e) => {
                const next = e.target.value as OnDuplicate;
                setOnDuplicate(next);
                setCommitted(null);
                if (file) void check(file.name, file.read, next);
              }}
              options={[
                { value: "fail", label: "Stop the import and tell me" },
                { value: "skip", label: "Leave it alone and import the rest" },
              ]}
            />
          </div>
          <p className="text-sm text-muted">{IMPORT_HINT[entity]}</p>

          {template.status === "loading" ? <Skeleton className="h-24 w-full" /> : null}
          {template.status === "error" ? (
            <ErrorState title="Couldn't load the columns" message={problemMessage(template.error)} onRetry={template.reload} />
          ) : null}
          {columns ? (
            <>
              <details className="rounded-tile border border-line p-3 text-sm">
                <summary className="cursor-pointer font-semibold">Columns</summary>
                <ul className="mt-2 flex flex-col gap-1">
                  {columns.map((c) => (
                    <li key={c.name}>
                      <span className="font-semibold">{c.name}</span>
                      {c.required ? <span className="text-negative"> *</span> : null} —{" "}
                      <span className="text-muted">{c.description}</span>
                    </li>
                  ))}
                </ul>
              </details>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="secondary" onClick={downloadSample}>
                  Download a sample CSV
                </Button>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  aria-label="Choose a CSV or Excel file"
                  className="text-sm"
                  onChange={(e) => void choose(e.target.files?.[0])}
                />
              </div>
            </>
          ) : null}

          {busy === "reading" || busy === "previewing" ? (
            <p role="status" className="text-sm text-muted">
              {busy === "reading" ? "Reading the file…" : "Checking every row…"}
            </p>
          ) : null}
          {message ? <InlineAlert tone="danger" title={message} /> : null}

          {file && file.read.ignored.length > 0 ? (
            <InlineAlert tone="info" title={`Ignored ${file.read.ignored.length} column${file.read.ignored.length === 1 ? "" : "s"}`}>
              {file.read.ignored.join(", ")} {file.read.ignored.length === 1 ? "is" : "are"} not an import column.
            </InlineAlert>
          ) : null}

          {preview ? (
            <div className="flex flex-col gap-3" aria-label="Preview">
              {preview.error_count === 0 ? (
                <InlineAlert tone="success" title={`${preview.total} rows are ready`}>
                  {preview.created} will be created
                  {preview.skipped > 0 ? `, ${preview.skipped} already exist and will be left alone` : ""}.
                  Nothing has been saved yet.
                </InlineAlert>
              ) : (
                <InlineAlert
                  tone="danger"
                  title={`${preview.error_count} row${preview.error_count === 1 ? "" : "s"} have problems`}
                >
                  Nothing is imported until every row is fixed. Correct the file and choose it again.
                </InlineAlert>
              )}
              {preview.errors.length > 0 ? (
                <ErrorTable errors={preview.errors} total={preview.error_count} />
              ) : null}
              <div className="flex flex-wrap gap-3">
                <Button
                  onClick={() => void commit()}
                  disabled={preview.error_count > 0 || busy !== null}
                  loading={busy === "committing"}
                >
                  Import {preview.created} row{preview.created === 1 ? "" : "s"}
                </Button>
                <Button variant="secondary" onClick={clear} disabled={busy === "committing"}>
                  Start over
                </Button>
              </div>
            </div>
          ) : null}

          {refused ? (
            <div className="flex flex-col gap-3">
              <InlineAlert tone="danger" title="Nothing was imported">
                The server found problems the preview did not (the data may have changed). Fix them and try again.
              </InlineAlert>
              <ErrorTable errors={refused} total={refused.length} />
            </div>
          ) : null}

          {committed ? (
            <InlineAlert tone="success" title={`Imported ${committed.created} row${committed.created === 1 ? "" : "s"}`}>
              {committed.skipped > 0 ? `${committed.skipped} already existed and were left alone. ` : ""}
              {committed.replayed ? "This import had already been made; nothing was added twice." : "Every row was saved together."}
              <div className="mt-3">
                <Button variant="secondary" onClick={clear}>
                  Import another file
                </Button>
              </div>
            </InlineAlert>
          ) : null}
        </Card>
      ) : (
        <EmptyState
          title="Importing needs permission"
          description="Ask an administrator for the bulk import permission, plus the right to create the thing you are importing."
        />
      )}

      {canExport ? (
        <Card className="flex flex-col gap-3 p-6">
          <h2 className="text-lg font-semibold">Export</h2>
          <p className="text-sm text-muted">
            A CSV of the whole list (up to 50,000 rows). Quantities are exact decimals; money is in minor units.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {(Object.keys(EXPORT_LABEL) as ExportEntity[]).map((target) => (
              <Button
                key={target}
                variant="secondary"
                loading={exporting === target}
                disabled={exporting !== null}
                onClick={() => void download(target)}
              >
                {EXPORT_LABEL[target]}
              </Button>
            ))}
          </div>
          {exportNote ? <p role="status" className="text-sm text-ink-soft">{exportNote}</p> : null}
        </Card>
      ) : null}
    </div>
  );
}
