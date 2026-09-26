import { useImportExport } from "@opsgrids/client";
import type { ImportEntity, ImportSummary } from "@opsgrids/client";
import { ImportExportButton } from "@opsgrids/ui";

interface HarnessProps<Payload, Item, Filters> {
  entity: ImportEntity<Payload, Item, Filters>;
  filters?: Filters | undefined;
  onImported?: ((summary: ImportSummary) => void) | undefined;
}

/**
 * What a list screen renders in its page header: the real Import / Export button wired to the real
 * hook. The screens pass their own filters and their list's reload; a test passes its own.
 */
export function Harness<Payload, Item, Filters>({ entity, filters, onImported }: HarnessProps<Payload, Item, Filters>) {
  const dx = useImportExport(entity, { filters, onImported });
  return <ImportExportButton meta={dx.meta} state={dx.state} actions={dx.actions} canImport canExport />;
}
