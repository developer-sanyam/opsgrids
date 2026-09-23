import { useRef, useState } from "react";

import { problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  FormDialog,
  InlineAlert,
  RowActions,
  Skeleton,
  Tag,
  TextField,
  useToast,
} from "@opsgrids/ui";
import type { Column, TagTone } from "@opsgrids/ui";

import { claimDomain, listDomains, releaseDomain, verifyDomain } from "@/api";
import type { Domain } from "@/api/types";
import { PageHeader } from "@/ui";

const WRITE_PERMISSION = "core.identity.domain.write";

/** `Verified` once `verified_at` is set — the claim clock stops mattering at that point.
 * Otherwise `Expired` past `expires_at`, or `Pending` while there is still time to publish
 * the record. Computed here, client-side, from what the server already sent: nothing about
 * ownership is decided on this side, only how the fact is worded. */
function statusOf(domain: Domain): { label: string; tone: TagTone } {
  if (domain.verified_at) return { label: "Verified", tone: "positive" };
  if (domain.expires_at && new Date(domain.expires_at).getTime() < Date.now()) {
    return { label: "Expired", tone: "negative" };
  }
  return { label: "Pending", tone: "warning" };
}

/** The periodic re-check found the record missing at least once since it last passed.
 * Deliberately not a status on its own — the domain is still verified, still usable for
 * sign-in, and this is a signal for an administrator to look at, not a state to gate
 * anything on. See `services.recheck_domain`'s own docstring for why nothing here revokes
 * automatically. */
function isCheckFailing(domain: Domain): boolean {
  return domain.verified_at !== null && domain.failing_since !== null;
}

export function DomainsPage() {
  const toast = useToast();
  const domains = useAsync(listDomains, []);
  const canWrite = useCan(WRITE_PERMISSION, "ORG");
  const [creating, setCreating] = useState(false);
  const [inspecting, setInspecting] = useState<Domain | null>(null);
  const [releasing, setReleasing] = useState<Domain | null>(null);

  const release = useMutation((domain: Domain) => releaseDomain(domain.id), {
    onSuccess: (_result, domain) => {
      setReleasing(null);
      toast.success(`${domain.domain} removed.`);
      domains.reload();
    },
  });

  if (domains.status === "loading") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          eyebrow="Administration"
          title="Domains"
          description="Email domains your organisation has proven it owns. A verified domain can route sign-in to an identity provider."
        />
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }

  if (domains.status === "error") {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          eyebrow="Administration"
          title="Domains"
          description="Email domains your organisation has proven it owns. A verified domain can route sign-in to an identity provider."
        />
        <ErrorState title="Couldn't load domains" message={problemMessage(domains.error)} onRetry={domains.reload} />
      </div>
    );
  }

  const columns: Column<Domain>[] = [
    {
      key: "domain",
      header: "Domain",
      render: (d) => (
        <span className="font-semibold text-ink">
          {d.domain}
          {d.is_primary ? (
            <Tag tone="info" className="ml-2">
              Primary
            </Tag>
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (d) => {
        const status = statusOf(d);
        return (
          <div className="flex flex-wrap items-center gap-1.5">
            <Tag tone={status.tone}>{status.label}</Tag>
            {isCheckFailing(d) ? <Tag tone="negative">Check failing</Tag> : null}
          </div>
        );
      },
    },
    {
      key: "claimed",
      header: "Claimed",
      render: (d) => new Date(d.created_at).toLocaleDateString(),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (d) => (
        <RowActions
          label={d.domain}
          actions={[
            { label: d.verified_at ? "View record" : "Publish record", onSelect: () => setInspecting(d) },
            { label: "Remove", tone: "danger", onSelect: () => setReleasing(d), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Administration"
        title="Domains"
        description="Email domains your organisation has proven it owns. A verified domain can route sign-in to an identity provider — never an unverified one."
        actions={canWrite ? <Button onClick={() => setCreating(true)}>Claim a domain</Button> : null}
      />

      <Card className="p-0">
        <DataTable
          rows={domains.data}
          rowKey={(d) => d.id}
          columns={columns}
          emptyState={
            <EmptyState
              title="No domains claimed yet"
              description="Claim your company's email domain to set up single sign-on. You'll need to publish a DNS TXT record to prove you own it."
              action={canWrite ? <Button onClick={() => setCreating(true)}>Claim a domain</Button> : null}
            />
          }
        />
      </Card>

      {creating ? (
        <ClaimDomainDialog
          onClose={() => setCreating(false)}
          onClaimed={(domain) => {
            domains.reload();
            setCreating(false);
            setInspecting(domain);
          }}
        />
      ) : null}

      {inspecting ? (
        <DomainRecordDialog
          domain={inspecting}
          onClose={() => setInspecting(null)}
          onChange={(domain) => {
            setInspecting(domain);
            domains.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={releasing !== null}
        onClose={() => {
          setReleasing(null);
          release.reset();
        }}
        onConfirm={() => releasing && void release.mutate(releasing)}
        title={`Remove ${releasing?.domain ?? "this domain"}?`}
        body="Nobody will be able to sign in through it any more, verified or not. This can't be undone."
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={release.pending}
        {...(release.error ? { error: problemMessage(release.error) } : {})}
      />
    </div>
  );
}

function ClaimDomainDialog({
  onClose,
  onClaimed,
}: {
  onClose: () => void;
  onClaimed: (domain: Domain) => void;
}) {
  const [domain, setDomain] = useState("");
  const [localError, setLocalError] = useState<string | undefined>(undefined);
  const domainRef = useRef<HTMLInputElement>(null);

  const claim = useMutation((value: string) => claimDomain({ domain: value }), {
    onSuccess: (result) => onClaimed(result),
  });

  function submit() {
    setLocalError(undefined);
    const value = domain.trim();
    if (value.length < 1) {
      setLocalError("Enter the domain you want to claim.");
      domainRef.current?.focus();
      return;
    }
    void claim.mutate(value);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Claim a domain"
      submitLabel="Claim domain"
      pendingLabel="Claiming…"
      pending={claim.pending}
      initialFocusRef={domainRef}
      {...(claim.error && Object.keys(claim.fieldErrors).length === 0
        ? { error: problemMessage(claim.error) }
        : {})}
    >
      <TextField
        ref={domainRef}
        label="Domain"
        placeholder="acme.com"
        value={domain}
        onChange={(e) => setDomain(e.target.value)}
        error={localError ?? claim.fieldErrors.domain}
        helper="You'll be shown a DNS TXT record to publish before this domain can be used for sign-in."
      />
    </FormDialog>
  );
}

function DomainRecordDialog({
  domain,
  onClose,
  onChange,
}: {
  domain: Domain;
  onClose: () => void;
  onChange: (domain: Domain) => void;
}) {
  const [copied, setCopied] = useState<"host" | "value" | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const status = statusOf(domain);

  const verify = useMutation(() => verifyDomain(domain.id), {
    onSuccess: (result) => onChange(result),
  });
  const reclaim = useMutation(() => claimDomain({ domain: domain.domain }), {
    onSuccess: (result) => onChange(result),
  });

  async function copy(field: "host" | "value", text: string) {
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(field);
    } catch {
      // No clipboard permission, an insecure origin, or a browser that never had one. The
      // value is on screen and selectable either way, so the honest answer is to say so
      // rather than leave a button that silently did nothing.
      setCopyFailed(true);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={domain.domain}
      description="Publish this TXT record at your DNS provider, then verify. DNS changes can take a while to propagate."
      footer={
        <div className="flex w-full items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <Tag tone={status.tone}>{status.label}</Tag>
            {isCheckFailing(domain) ? <Tag tone="negative">Check failing</Tag> : null}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>
              Close
            </Button>
            {domain.verified_at ? null : status.label === "Expired" ? (
              <Button onClick={() => void reclaim.mutate(undefined)} disabled={reclaim.pending}>
                {reclaim.pending ? "Claiming…" : "Claim again"}
              </Button>
            ) : (
              <Button onClick={() => void verify.mutate(undefined)} disabled={verify.pending}>
                {verify.pending ? "Verifying…" : "Verify now"}
              </Button>
            )}
          </div>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {isCheckFailing(domain) ? (
          <InlineAlert tone="warning" title="The DNS record could not be found on a recent check.">
            {domain.domain} is still verified and still usable for sign-in — this is not
            revoked automatically. If the record was removed deliberately, remove the domain
            below; otherwise republish it and it will clear on the next check.
          </InlineAlert>
        ) : domain.verified_at ? (
          <InlineAlert tone="success" title="This domain is verified.">
            It can be used to route single sign-in to an identity provider.
          </InlineAlert>
        ) : status.label === "Expired" ? (
          <InlineAlert tone="warning" title="This claim has expired.">
            Claim the domain again to get a fresh record to publish.
          </InlineAlert>
        ) : null}
        {(verify.error || reclaim.error) && status.label !== "Expired" ? (
          <InlineAlert tone="danger" title="That didn't verify.">
            {problemMessage((verify.error ?? reclaim.error) as Error)}
          </InlineAlert>
        ) : null}

        <Field
          label="Record name"
          value={domain.verification_host}
          copied={copied === "host"}
          onCopy={() => void copy("host", domain.verification_host)}
        />
        <Field
          label="Record value"
          value={domain.verification_value}
          copied={copied === "value"}
          onCopy={() => void copy("value", domain.verification_value)}
        />
        <p className="text-xs text-faint">Type: TXT</p>
        {copyFailed ? (
          <p role="status" className="text-xs text-negative">
            Couldn't copy that. Select the text above and copy it yourself.
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function Field({
  label,
  value,
  copied,
  onCopy,
}: {
  label: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</span>
      <div className="flex items-center gap-2 rounded-tile bg-ground px-4 py-3">
        <code className="min-w-0 flex-1 truncate text-sm text-ink">{value}</code>
        <Button type="button" variant="outline" size="sm" onClick={onCopy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}
