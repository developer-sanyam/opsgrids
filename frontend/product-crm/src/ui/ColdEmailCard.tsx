import { problemMessage, useAsync, useCan, useEntitlement } from "@opsgrids/client";

import { getLeadColdEmails } from "@/api/coldEmail";
import { formatRelative } from "@/lib/format";
import { Card, ErrorState, Skeleton } from "@/ui";

function Loaded({ leadId }: { leadId: string }) {
  const state = useAsync(() => getLeadColdEmails(leadId), [leadId]);
  // A lead nobody has emailed shows no card at all.
  if (state.status === "success" && state.data.length === 0) return null;
  return (
    <Card className="flex flex-col gap-3 p-6">
      <h2 className="text-base font-semibold text-ink">Cold emails</h2>
      {state.status === "loading" ? (
        <div aria-busy="true">
          <span className="sr-only">Loading cold emails</span>
          <Skeleton className="h-10 w-full" />
        </div>
      ) : null}
      {state.status === "error" ? (
        <ErrorState title="Couldn't load cold emails" message={problemMessage(state.error)} onRetry={state.reload} />
      ) : null}
      {state.status === "success" ? (
        <ul className="flex flex-col divide-y divide-line">
          {state.data.map((entry) => (
            <li key={`${entry.campaign_id}-${entry.at}`} className="flex items-start justify-between gap-3 py-3">
              <span className="min-w-0 break-words text-sm text-ink">{entry.title}</span>
              <span className="shrink-0 text-xs text-muted">{formatRelative(entry.at)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

/**
 * The cold emails sent to this lead: the campaign subject only, never the body or the address.
 * Hidden without the Engage entitlement or the permission to read campaigns; the server decides
 * what is returned either way, and also checks the lead is one this person may see.
 */
export function ColdEmailCard({ leadId }: { leadId: string }) {
  const entitled = useEntitlement("engage");
  const allowed = useCan("engage.campaign.campaign.read", "ORG");
  if (!entitled || !allowed) return null;
  return <Loaded leadId={leadId} />;
}
