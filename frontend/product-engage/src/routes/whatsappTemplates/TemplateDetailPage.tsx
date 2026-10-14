import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import { ArrowLeftIcon, StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

import type { TemplateQuality } from "@/api/whatsappTemplates";
import { failureMessage, isNotActivated } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  EngageNotActive,
  ErrorState,
  PageHeader,
  Skeleton,
  TemplateStatusTag,
} from "@/ui";

import { CATEGORY_LABEL, languageLabel, QUALITY_LABEL, templateName } from "./labels";
import { TemplateDetailBody } from "./TemplateDetailBody";
import { useTemplateView } from "./useTemplateView";

const LIST_PATH = "/whatsapp/templates";

/** Meta's quality rating has its own tones: `high` is good here, unlike the registry's `high`. */
const QUALITY_TONE: Record<TemplateQuality, StatusTone> = {
  high: "success",
  medium: "warning",
  low: "danger",
  unknown: "neutral",
};

function BackLink() {
  return (
    <Link
      to={LIST_PATH}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <ArrowLeftIcon className="size-4" />
      All templates
    </Link>
  );
}

function DetailSkeleton() {
  return (
    <div aria-busy="true" className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <span className="sr-only" role="status">
        Loading template
      </span>
      <Card className="flex flex-col gap-4 p-6">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-40 w-full rounded-tile" />
      </Card>
      <Card className="flex flex-col gap-4 p-6 lg:col-span-2">
        <Skeleton className="h-32 w-full rounded-card" />
        <Skeleton className="h-24 w-full rounded-tile" />
      </Card>
    </div>
  );
}

function TemplateDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const state = useTemplateView(id);

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <DetailSkeleton />
      </div>
    );
  }

  if (state.status === "error") {
    if (isNotActivated(state.error)) return <EngageNotActive />;
    const missing = state.error instanceof ApiError && state.error.status === 404;
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink />
        <Card className="p-6">
          <ErrorState
            title={missing ? "That template doesn't exist" : "Couldn't load this template"}
            message={missing ? "It may have been removed at Meta. Sync your templates to refresh the list." : failureMessage(state.error)}
            {...(missing
              ? { action: <Button variant="secondary" onClick={() => navigate(LIST_PATH)}>Back to templates</Button> }
              : { onRetry: state.reload })}
          />
        </Card>
      </div>
    );
  }

  const { detail } = state.data;
  const facts: [string, string][] = [
    ["Category", CATEGORY_LABEL[detail.category]],
    ["Language", languageLabel(detail.language)],
    ["Meta template ID", detail.meta_template_id ?? "Not assigned yet"],
    ["Submitted", detail.submitted_at === null ? "Not submitted" : formatInstant(detail.submitted_at)],
    ["Last updated", formatInstant(detail.updated_at)],
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />
      <PageHeader eyebrow="WhatsApp template" title={templateName(detail)} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="flex flex-col gap-4 p-6">
          <h2 className="text-section text-ink">Details</h2>
          <div className="flex flex-wrap items-center gap-2">
            <TemplateStatusTag status={detail.status} />
            <StatusBadge tone={QUALITY_TONE[detail.quality]} label={QUALITY_LABEL[detail.quality]} />
          </div>
          <dl className="flex flex-col gap-3 text-body-sm">
            {facts.map(([term, value]) => (
              <div key={term} className="flex flex-col gap-1">
                <dt className="text-muted">{term}</dt>
                <dd className="break-words font-semibold text-ink tnum">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card className="p-6 lg:col-span-2">
          <TemplateDetailBody view={state.data} level="h2" />
        </Card>
      </div>
    </div>
  );
}

/** `/whatsapp/templates/:id`. */
export function TemplateDetailPage() {
  const { id } = useParams();
  return <Announcer>{id === undefined ? null : <TemplateDetail id={id} />}</Announcer>;
}
