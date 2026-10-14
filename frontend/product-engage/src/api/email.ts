/**
 * The email area's API surface: the message templates `notification` holds (read and written
 * through Engage's own route), the per-status campaign counts the list tabs show, and the
 * connection as the email settings screen reads it.
 *
 * Everything here is live: none of it has fixtures, because none of it is reachable in fixture
 * mode's offline design work without inventing rows the service never returned.
 */

import { apiRequest } from "@opsgrids/client";

import { FULL_PAGE, listCampaigns } from "./client";
import { USE_FIXTURES, fixturePage, refuseWithoutService, settle } from "./client";
import { EMAIL_TEMPLATES } from "./sample.fixtures";
import type { CampaignStatus, Page } from "./types";
export type { EmailTemplateOut, EmailTemplateIn, CampaignCounts, EmailAccount } from "./email.types";
import type { EmailTemplateOut, EmailTemplateIn, CampaignCounts } from "./email.types";

/** The service's own limits on `TemplateIn`, so the form can say them before the round trip. */
export const TEMPLATE_CODE_PATTERN = /^[a-z0-9_]+$/;
export const TEMPLATE_CODE_MAX = 80;
export const TEMPLATE_SUBJECT_MAX = 300;

/** Every email template in the organization — one full page, the same working-set approach the
 *  other Engage lists take. */
export async function listEmailTemplates(): Promise<Page<EmailTemplateOut>> {
  if (USE_FIXTURES) return settle(fixturePage(EMAIL_TEMPLATES));
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  return apiRequest<Page<EmailTemplateOut>>(`/engage/email/templates?${query.toString()}`);
}

/** Create or replace an email template — `PUT /engage/email/templates`. */
export async function saveEmailTemplate(input: EmailTemplateIn): Promise<EmailTemplateOut> {
  if (USE_FIXTURES) refuseWithoutService("Saving an email template");
  return apiRequest<EmailTemplateOut>("/engage/email/templates", { method: "PUT", body: input });
}

const COUNTED: CampaignStatus[] = ["draft", "scheduled", "sending", "paused", "sent", "cancelled"];

/**
 * How many campaigns sit in each status — one one-row page per status, read for its `total`.
 * The service has no counts route; `total` in the envelope is the count of the filtered query, so
 * the tabs agree with the list they open at any organization size. `all` is their sum.
 */
export async function countCampaignsByStatus(): Promise<CampaignCounts> {
  const totals = await Promise.all(
    COUNTED.map(async (status) => (await listCampaigns({ status, page: 1, size: 1 })).total),
  );
  const counts: CampaignCounts = {
    all: 0,
    draft: 0,
    scheduled: 0,
    sending: 0,
    paused: 0,
    sent: 0,
    cancelled: 0,
  };
  COUNTED.forEach((status, index) => {
    const total = totals[index] ?? 0;
    counts[status] = total;
    counts.all += total;
  });
  return counts;
}