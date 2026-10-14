/** The request and response shapes of `./email`, kept apart so fixtures can import them without importing the requests. */
import type { CampaignStatus, ConnectionOut } from "./types";

/**
 * One email template, as `GET /engage/email/templates` returns it (`TemplateOut` in
 * `services/notification/app/schemas.py`). `id` is the template's code: the service keys a
 * template by `(code, channel)` and has no separate identifier.
 *
 * Mounted under `/api/v1/engage`, gated on `engage.email.template.read` — its own Engage
 * permission, held by every Engage seat by default. This used to call `notification`'s own
 * `/notifications/templates`, gated on a `notification`-catalogue permission no Engage role ever
 * granted: every Engage customer, including one with no `notification` seat at all, saw this
 * screen fail with "Missing permission: notification.delivery.template.read". The row is the
 * same `Template` either way — only the permission that reaches it changed.
 *
 * There is no category, status, language or "used in" on this row, and this screen does not
 * pretend there is: what the service does not send is not shown.
 */
export interface EmailTemplateOut {
  id: string;
  channel: "email";
  subject: string | null;
  body: string;
  description: string;
  /** Every `{{placeholder}}` the subject and body name, derived by the service. */
  placeholders: string[];
  created_at: string;
  updated_at: string;
}

/**
 * `PUT /engage/email/templates` — creates the template or replaces the one with this code.
 * Gated on `engage.email.template.write`, held by `ENGAGE_ADMIN` alone.
 */
export interface EmailTemplateIn {
  code: string;
  channel: "email";
  subject?: string;
  body: string;
  description?: string;
}

export type CampaignCounts = Record<CampaignStatus | "all", number>;

/**
 * The connection as the settings screen reads it. `imap_enabled` is E2's addition to the payload;
 * it is absent until that ships, and the screen shows nothing about inbox sync while it is.
 */
export type EmailAccount = ConnectionOut & { imap_enabled?: boolean };
