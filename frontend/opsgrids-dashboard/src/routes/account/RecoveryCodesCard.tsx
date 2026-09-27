import { Button, Card, InlineAlert } from "@opsgrids/ui";

/**
 * The recovery codes for an account's **first** authenticator, shown once.
 *
 * Only that confirmation returns any: an authenticator added beside an existing one leaves the
 * codes the person already saved exactly as they were, and the caller does not render this card
 * for it — an empty "save these" panel would tell them something that is not true.
 */
export function RecoveryCodesCard({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="text-lg font-bold tracking-tight">Recovery codes</h2>
      <InlineAlert tone="warning" title="Shown only once — save these somewhere safe.">
        Each one signs you in if you lose access to your authenticator app.
      </InlineAlert>
      <ul className="grid grid-cols-2 gap-2 rounded-tile bg-ground p-4 font-mono text-sm tabular-nums">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <Button type="button" onClick={onDone} className="self-start">
        I&apos;ve saved these codes
      </Button>
    </Card>
  );
}
