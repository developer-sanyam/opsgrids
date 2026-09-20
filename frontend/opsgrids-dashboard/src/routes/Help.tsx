import { Card, PageHeader } from "@/ui";

/** Where to get help. The phone numbers are the ones on the marketing site. */
export function HelpPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Help" title="Talk to a person" />
      <Card className="flex flex-col gap-3 p-8">
        <p className="max-w-prose text-sm text-ink-soft">
          Every trial comes with someone you can call. If a product does not do what the page said, tell us — that is a
          bug we fix, not a feature we sell around.
        </p>
        <ul className="flex list-none flex-col gap-1 p-0 text-sm">
          <li>
            <a href="tel:+919019527568" className="font-semibold text-ink underline-offset-2 hover:underline">
              +91 90195 27568
            </a>
          </li>
          <li>
            <a href="tel:+918949263849" className="font-semibold text-ink underline-offset-2 hover:underline">
              +91 89492 63849
            </a>
          </li>
        </ul>
      </Card>
    </div>
  );
}
