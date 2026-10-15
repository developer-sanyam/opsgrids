import { useNavigate } from "react-router-dom";

import type { SupplierField } from "@/api/types";
import { Button, Card } from "@/ui";
import { ShieldCheckIcon } from "@opsgrids/ui";

import { SUPPLIER_FIELD_LABEL } from "./vocabulary";


/**
 * Shown instead of the invoice form while the organization's supplier details are incomplete. A
 * tax invoice must state the supplier's legal name, address and GSTIN (CGST Rule 46(a)), so the
 * form is not offered at all rather than producing a document that is missing them.
 */
export function SupplierSetupPrompt({ missing, canConfigure }: { missing: readonly SupplierField[]; canConfigure: boolean }) {
  const navigate = useNavigate();
  return (
    <Card className="flex flex-col items-start gap-4 p-6">
      <ShieldCheckIcon className="size-6 text-muted" />
      <div className="flex flex-col gap-1">
        <h2 className="text-section font-bold text-ink">Complete your supplier details first</h2>
        <p className="max-w-prose text-sm text-muted">
          A tax invoice has to state who is supplying: your legal name, registered address and
          GSTIN. These are still missing:
        </p>
      </div>
      <ul className="list-disc pl-4 text-sm text-ink-soft">
        {missing.map((field) => (
          <li key={field}>{SUPPLIER_FIELD_LABEL[field]}</li>
        ))}
      </ul>
      {canConfigure ? (
        <Button onClick={() => navigate("/settings")}>Open Finance settings</Button>
      ) : (
        <p className="text-sm text-muted">Ask a finance administrator to fill these in under Settings.</p>
      )}
    </Card>
  );
}
