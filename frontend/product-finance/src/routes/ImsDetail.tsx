import { useCallback, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { decideImsDocument, getImsDocument } from "@/api/client";
import type {
  ImsAction,
  ImsDecision,
  ImsDocument,
  ImsDocumentDetail,
  ImsExpenseCategory,
} from "@/api/types";
import {
  actionConsequence,
  actionLabel,
  actionPastTense,
  reasonLabel,
  suggestionSentence,
} from "@/data/compliance";
import { readErrorMessage } from "@/lib/errors";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatAmount, formatMoney } from "@/lib/money";
import { Can, useAsync, useAuth } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  ErrorState,
  ImsStatusPill,
  SampleDataChip,
  SectionHeader,
  Select,
  Skeleton,
  SuggestedAction,
  Tag,
  PortalSimulatedNotice,
} from "@/ui";
import { ArrowLeftIcon, CheckCircleIcon, cn } from "@opsgrids/ui";
import type { Minor } from "@/lib/money";

const DECISIONS: ImsAction[] = ["accept", "reject", "pending_action"];

/**
 * What an accepted document was for, and what each blocks under GST s. 17(5). `general` is the
 * only category that ever accrues credit; every other one is offered so the person accepting a
 * document can say why it does not, rather than the platform silently guessing "eligible" for
 * everything since nothing here carries an HSN or a description to guess from.
 */
const EXPENSE_CATEGORIES: { value: ImsExpenseCategory; label: string; blocked: boolean }[] = [
  { value: "general", label: "General business purchase", blocked: false },
  { value: "motor_vehicle", label: "Motor vehicle (s. 17(5)(a))", blocked: true },
  { value: "food_beverage", label: "Food, beverage or insurance (s. 17(5)(b)(i))", blocked: true },
  { value: "membership_club_fitness", label: "Club or fitness membership (s. 17(5)(b)(ii))", blocked: true },
  { value: "works_contract_immovable", label: "Works contract / construction (s. 17(5)(c)/(d))", blocked: true },
  { value: "personal_consumption", label: "Personal consumption (s. 17(5)(g))", blocked: true },
];

/** One side of the comparison, flattened so the two can be lined up field for field. */
interface EvidenceSide {
  heading: string;
  caption: string;
  gstin: string;
  number: string;
  date: string;
  taxable: Minor;
  cgst: Minor;
  sgst: Minor;
  igst: Minor;
  total: Minor;
}

function sideFromDocument(document: ImsDocument, heading: string, caption: string): EvidenceSide {
  return {
    heading,
    caption,
    gstin: document.supplier_gstin,
    number: document.document_number,
    date: document.document_date,
    taxable: document.taxable_minor,
    cgst: document.cgst_minor,
    sgst: document.sgst_minor,
    igst: document.igst_minor,
    total: document.total_minor,
  };
}

/**
 * The evidence, side by side.
 *
 * An operator asked to accept or reject somebody else's claim on this business needs to see the
 * document it was matched against — not a verdict they can only take on trust. Where the two
 * disagree the row says so in words as well as in weight, because a highlighted cell alone is a
 * difference only sighted users can find.
 */
function EvidenceTable({ left, right }: { left: EvidenceSide; right: EvidenceSide | null }) {
  const fields: { label: string; get: (side: EvidenceSide) => string; money?: boolean }[] = [
    { label: "Supplier GSTIN", get: (s) => s.gstin },
    { label: "Document number", get: (s) => s.number },
    { label: "Document date", get: (s) => formatDate(s.date) },
    { label: "Taxable value", get: (s) => `₹${formatAmount(s.taxable)}`, money: true },
    { label: "CGST", get: (s) => `₹${formatAmount(s.cgst)}`, money: true },
    { label: "SGST", get: (s) => `₹${formatAmount(s.sgst)}`, money: true },
    { label: "IGST", get: (s) => `₹${formatAmount(s.igst)}`, money: true },
    { label: "Total", get: (s) => `₹${formatAmount(s.total)}`, money: true },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">
          {left.heading} compared with {right?.heading ?? "your own records"}
        </caption>
        <thead>
          <tr className="border-b border-line text-left align-bottom">
            <th scope="col" className="py-2.5 pr-3 text-xs font-semibold uppercase tracking-wide text-faint">
              Field
            </th>
            <th scope="col" className="py-2.5 px-3">
              <span className="block text-sm font-bold text-ink">{left.heading}</span>
              <span className="block text-xs font-medium text-muted">{left.caption}</span>
            </th>
            <th scope="col" className="py-2.5 pl-3">
              <span className="block text-sm font-bold text-ink">
                {right?.heading ?? "Your record"}
              </span>
              <span className="block text-xs font-medium text-muted">
                {right?.caption ?? "Nothing on file"}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => {
            const leftValue = field.get(left);
            const rightValue = right === null ? null : field.get(right);
            const differs = rightValue !== null && rightValue !== leftValue;
            return (
              <tr key={field.label} className="border-b border-line/70 last:border-0">
                <th
                  scope="row"
                  className="py-2.5 pr-3 text-left text-xs font-semibold uppercase tracking-wide text-faint"
                >
                  {field.label}
                  {differs ? <span className="sr-only"> — the two disagree</span> : null}
                </th>
                <td
                  className={cn(
                    "py-2.5 px-3 align-top",
                    field.money ? "money tnum" : "money",
                    differs ? "font-bold text-ink" : "text-ink-soft",
                  )}
                >
                  {leftValue}
                </td>
                <td
                  className={cn(
                    "py-2.5 pl-3 align-top",
                    field.money ? "money tnum" : "money",
                    differs ? "font-bold text-ink" : "text-ink-soft",
                  )}
                >
                  {rightValue ?? <span className="text-faint">—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** One recorded decision, with what was suggested at the time and whether it was followed. */
function DecisionRow({ decision }: { decision: ImsDecision }) {
  const followed =
    decision.suggested_action !== null && decision.suggested_action === decision.action;
  return (
    <li className="flex gap-3 border-b border-line/70 py-3 last:border-0">
      <Avatar name={decision.decided_by} size="md" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-sm font-bold text-ink">{decision.decided_by}</span>
          <span className="text-sm text-ink-soft">{actionPastTense(decision.action)}</span>
          <time className="text-xs text-muted tnum" dateTime={decision.decided_at}>
            {formatDateTime(decision.decided_at)}
          </time>
        </div>
        {decision.suggested_action !== null ? (
          <span className="text-xs text-muted">
            The platform suggested {actionLabel(decision.suggested_action).toLowerCase()}
            {decision.suggestion_reason ? ` (${reasonLabel(decision.suggestion_reason).toLowerCase()})` : ""}
            {followed ? " — followed." : " — overruled."}
          </span>
        ) : null}
        <span className="text-xs text-muted">
          {expenseCategoryLabel(decision.expense_category)}
        </span>
        {decision.note ? <p className="max-w-prose pt-1 text-sm text-ink-soft">{decision.note}</p> : null}
      </div>
    </li>
  );
}

function expenseCategoryLabel(category: ImsExpenseCategory): string {
  const found = EXPENSE_CATEGORIES.find((c) => c.value === category);
  if (found === undefined) return category;
  return found.blocked ? `${found.label} — no ITC claimed` : `${found.label} — ITC eligible`;
}

/**
 * The decision form.
 *
 * Three choices, each stating what it does to this organization's money, and one submit. The
 * platform's suggestion arrives pre-selected and labelled as a suggestion — which is as far as a
 * machine should get on a question of who owes what. There is no accept-all and no one-click
 * accept from the queue: every credit claimed here has a person's name against it.
 */
function DecideForm({
  document,
  onDecided,
}: {
  document: ImsDocument;
  onDecided: (decision: ImsDecision) => void;
}) {
  const { user } = useAuth();
  const suggested = document.suggestion?.suggested_action ?? null;
  const [action, setAction] = useState<ImsAction>(suggested ?? "pending_action");
  // A re-decision (rejecting an earlier acceptance, or the reverse) starts from what was already
  // decided, not a hardcoded default — a second look at a document should not silently reset the
  // category a person already chose. Only a document with no decision yet falls back to `general`.
  const [expenseCategory, setExpenseCategory] = useState<ImsExpenseCategory>(
    document.decision?.expense_category ?? "general",
  );
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "saving">("idle");
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      setState("saving");
      setError(null);
      decideImsDocument(document.id, {
        action,
        note: note.trim() === "" ? null : note.trim(),
        expenseCategory,
        decidedBy: user?.full_name ?? "You",
      })
        .then((decision) => {
          setState("idle");
          setNote("");
          onDecided(decision);
        })
        .catch((err: unknown) => {
          setState("idle");
          setError(
            err instanceof Error ? err.message : "The decision could not be recorded.",
          );
        });
    },
    [action, note, expenseCategory, document.id, onDecided, user],
  );

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="pb-2 text-sm font-bold text-ink">Your decision</legend>
        {DECISIONS.map((option) => (
          <label
            key={option}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-tile border p-3 transition-colors",
              action === option
                ? "border-accent bg-accent-soft/50"
                : "border-line hover:bg-ground",
            )}
          >
            <input
              type="radio"
              name="ims-decision"
              value={option}
              checked={action === option}
              onChange={() => setAction(option)}
              className="mt-0.5 size-4 accent-accent"
            />
            <span className="flex flex-col gap-0.5">
              <span className="flex items-center gap-2 text-sm font-bold text-ink">
                {actionLabel(option)}
                {suggested === option ? <Tag>Suggested</Tag> : null}
              </span>
              <span className="text-xs text-muted">{actionConsequence(option)}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <Select
        label="What was this for?"
        value={expenseCategory}
        onChange={(event) => setExpenseCategory(event.target.value as ImsExpenseCategory)}
        options={EXPENSE_CATEGORIES.map(({ value, label }) => ({ value, label }))}
        helper={
          EXPENSE_CATEGORIES.find((c) => c.value === expenseCategory)?.blocked
            ? "Blocked under s. 17(5) — accepting this document claims no input tax credit."
            : "Eligible — accepting this document claims the input tax credit on it."
        }
      />

      <div className="flex flex-col gap-1.5">
        <label htmlFor="ims-note" className="text-xs font-semibold uppercase tracking-wide text-faint">
          Note (optional)
        </label>
        <textarea
          id="ims-note"
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Why you decided this way — it stays on the record."
          className="rounded-tile bg-ground px-3 py-2 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
        />
        <p className="text-xs text-muted">
          Free text: the portal takes no reason code for an IMS action, and a dropdown would only
          invite an untrue answer.
        </p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm font-medium text-negative">
          {error}
        </p>
      ) : null}

      <Button type="submit" size="md" className="self-start" disabled={state === "saving"}>
        {state === "saving" ? "Recording…" : "Record decision"}
      </Button>
    </form>
  );
}

/**
 * The receipt for a decision just taken.
 *
 * Lives above `DecideForm` rather than inside it, and is owned by the page: recording a decision
 * reloads the document, the body unmounts while that read is in flight, and a confirmation that
 * lives inside the form would flash away with it. Feedback that only appears when the network is
 * slow is worse than none.
 */
function DecisionReceipt({ decision }: { decision: ImsDecision }) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-tile bg-present-soft px-4 py-3 text-present"
    >
      <CheckCircleIcon className="mt-0.5 size-5 shrink-0" />
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-bold">Decision recorded — {actionLabel(decision.action)}</span>
        <span className="text-xs text-present/90">
          Appended to this document's history with your name and the time. A change of mind is a
          further decision below, never an edit to this one.
        </span>
      </div>
    </div>
  );
}

function DetailBody({
  detail,
  justDecided,
  onDecided,
}: {
  detail: ImsDocumentDetail;
  justDecided: ImsDecision | null;
  onDecided: (decision: ImsDecision) => void;
}) {
  const { document, matched_purchase: purchase, duplicate_of: duplicate, decisions } = detail;
  const suggestion = document.suggestion ?? null;

  const left = sideFromDocument(
    document,
    "What the supplier filed",
    `Portal reference ${document.source_ref}`,
  );
  const right: EvidenceSide | null = duplicate
    ? sideFromDocument(duplicate, "The earlier arrival", `Portal reference ${duplicate.source_ref}`)
    : purchase
      ? {
          heading: "Your purchase record",
          caption: purchase.number,
          gstin: purchase.supplier_gstin,
          number: purchase.number,
          date: purchase.document_date,
          taxable: purchase.taxable_minor,
          cgst: purchase.cgst_minor,
          sgst: purchase.sgst_minor,
          igst: purchase.igst_minor,
          total: purchase.total_minor,
        }
      : null;

  return (
    <div className="flex flex-col gap-6">
      <PortalSimulatedNotice />
      <Card className="flex flex-col gap-5 p-6 lg:p-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="display text-2xl font-extrabold">{document.supplier_name}</h1>
              <ImsStatusPill status={document.status} />
              <SampleDataChip />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Tag className="money">{document.supplier_gstin}</Tag>
              <Tag className="money">{document.document_number}</Tag>
              <Tag>{formatDate(document.document_date)}</Tag>
              <Tag>Period {document.period}</Tag>
            </div>
          </div>
          <div className="flex flex-col items-end gap-0.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              They declare
            </span>
            <span className="money text-2xl font-extrabold text-ink">
              {formatMoney(document.total_minor)}
            </span>
            {document.irn ? (
              <span className="money max-w-[16rem] truncate text-xs text-muted" title={document.irn}>
                IRN {document.irn}
              </span>
            ) : (
              <span className="text-xs text-muted">The supplier did not e-invoice this</span>
            )}
          </div>
        </div>

        {suggestion !== null ? (
          <div className="flex flex-col gap-2 rounded-tile bg-ground p-4">
            <div className="flex flex-wrap items-center gap-2">
              <SuggestedAction action={suggestion.suggested_action} />
              <Tag>{reasonLabel(suggestion.reason)}</Tag>
              <span className="text-xs text-muted tnum">
                reconciled {formatDateTime(suggestion.computed_at)}
              </span>
            </div>
            <p className="max-w-prose text-base font-semibold text-ink">
              {suggestionSentence(suggestion)}
            </p>
            <p className="max-w-prose text-xs text-muted">
              A suggestion, not a decision. Nothing is claimed or declined until you record one
              below.
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted">
            No reconciliation has run against this document yet, so the platform suggests nothing.
          </p>
        )}
      </Card>

      <Card className="flex flex-col gap-4 p-6 lg:p-8">
        <SectionHeader
          title="The evidence"
          subtitle={
            duplicate
              ? "This arrival against the copy that reached you first"
              : purchase
                ? "What the supplier filed against what you recorded buying"
                : "What the supplier filed — nothing on your side matches it"
          }
        />
        <EvidenceTable left={left} right={right} />
        {right === null ? (
          <p className="max-w-prose text-sm text-muted">
            {suggestion?.reason === "supplier_unknown"
              ? "No purchase from this GSTIN is on file at all. A document from a supplier you have never bought from is the classic mis-addressed filing — and occasionally something worse."
              : "No purchase document with this number is recorded for this financial year. Usually a timing difference: the goods arrived, the paperwork has not."}
          </p>
        ) : null}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 items-start">
        <Card className="flex flex-col gap-4 p-6">
          <SectionHeader
            title="Decide"
            subtitle="The platform suggests; you decide, and the record says so"
          />
          {justDecided !== null ? <DecisionReceipt decision={justDecided} /> : null}
          <Can
            permission="finance.compliance.ims.decide"
            scope="ORG"
            fallback={
              <p className="text-sm text-muted">
                You can see this queue but not decide it — deciding is a separate permission
                (<span className="money">finance.compliance.ims.decide</span>). Ask an
                administrator if you need it.
              </p>
            }
          >
            <DecideForm document={document} onDecided={onDecided} />
          </Can>
        </Card>

        <Card className="flex flex-col gap-3 p-6">
          <SectionHeader
            title="Decision history"
            subtitle="Append-only — every decision, newest first"
          />
          {decisions.length === 0 ? (
            <p className="py-6 text-sm text-muted">
              Nobody has decided this one yet. That is what an undecided row is.
            </p>
          ) : (
            <ul className="flex flex-col">
              {decisions.map((decision) => (
                <DecisionRow key={decision.id} decision={decision} />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Loading(): ReactNode {
  return (
    <div className="flex flex-col gap-6">
      <Card className="flex flex-col gap-4 p-6 lg:p-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-20 w-full rounded-tile" />
      </Card>
      <Card className="p-6 lg:p-8">
        <Skeleton className="h-56 w-full rounded-tile" />
      </Card>
    </div>
  );
}

export function ImsDetailPage() {
  const { id = "" } = useParams();
  // Bumped after a decision so the document, its status and its history reload together — one
  // source of truth rather than a locally patched row that can drift from the server's.
  const [reloadToken, setReloadToken] = useState(0);
  const [justDecided, setJustDecided] = useState<ImsDecision | null>(null);
  const loader = useCallback(() => getImsDocument(id), [id]);
  const detail = useAsync(loader, [id, reloadToken]);

  const onDecided = useCallback((decision: ImsDecision) => {
    setJustDecided(decision);
    setReloadToken((n) => n + 1);
  }, []);

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/compliance/ims"
        className="inline-flex items-center gap-1.5 self-start text-sm font-semibold text-muted transition-colors hover:text-ink"
      >
        <ArrowLeftIcon className="size-4" />
        Back to the reconciliation queue
      </Link>

      {detail.status === "loading" ? <Loading /> : null}
      {detail.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={readErrorMessage(detail.error, "this supplier document")} />
        </Card>
      ) : null}
      {detail.status === "success" ? (
        <DetailBody detail={detail.data} justDecided={justDecided} onDecided={onDecided} />
      ) : null}
    </div>
  );
}
