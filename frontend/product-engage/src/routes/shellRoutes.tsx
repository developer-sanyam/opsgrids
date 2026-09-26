import type { ReactNode } from "react";
import type { RouteObject } from "react-router-dom";

import { RequireGrant } from "@/auth/RequireGrant";

import { CampaignPage, NewCampaignPage } from "./CampaignEditor";
import { CampaignsPage } from "./Campaigns";
import { ConnectionPage } from "./Connection";
import { ContactListMembersPage } from "./ContactListMembers";
import { ContactListsPage } from "./ContactLists";
import { ContactsPage } from "./Contacts";
import { DomainsPage } from "./Domains";
import { InboxPage } from "./Inbox";
import { NotFoundPage } from "./NotFound";
import { OverviewPage } from "./Overview";
import { SendersPage } from "./Senders";
import { SuppressionsPage } from "./Suppressions";
import { WhatsAppConnectionPage } from "./WhatsAppConnection";

/**
 * The screens the shell serves itself, next to the ones each feature area contributes
 * (`areaRoutes`): the six sending-identity screens Phase 1 shipped with, the audience — contacts,
 * lists and suppressions — the inbox, the two-way half of WhatsApp, and campaigns with their
 * report. A URL nothing routes to lands on the 404 that tells the truth rather than on a panel
 * that promises.
 *
 * `/connection` is singular because the resource is: one organization gets one connection, and the
 * service refuses a second. The navigation says "Connections" — the customer is looking for where
 * connections live — while the address says what is actually there. `/inbox` carries state in its
 * query string (`?id=`, `?status=`) rather than a path segment, so a link to one conversation is
 * shareable and survives a refresh without a `conversations/:id` route this slice does not add.
 *
 * Lists and campaigns *do* have a path segment, because each is a record with its own page: a
 * list's members, and a campaign's form (while it is a draft) or its report (once it has left
 * `draft`). `campaigns/new` is declared before `campaigns/:campaignId` so it is never read as an
 * id. Both are behind a `RequireGrant` on their read permission, which renders the denial rather
 * than the page — presentation only: the pages also hide the controls the caller cannot use, and
 * the service refuses every call behind them regardless.
 *
 * This list lives here, not in `main.tsx`, so a test can ask which paths are registered and check
 * that a link somewhere else in the app leads to one of them.
 */

/** A screen behind its read permission. Plain functions rather than components: they are called
 *  once, at route-table construction, and hold no state. */
const listsGate = (page: ReactNode) => (
  <RequireGrant permission="engage.audience.list.read" what="lists">
    {page}
  </RequireGrant>
);

const campaignsGate = (page: ReactNode) => (
  <RequireGrant permission="engage.campaign.campaign.read" what="campaigns">
    {page}
  </RequireGrant>
);

export const shellRoutes: RouteObject[] = [
  { path: "setup", element: <OverviewPage /> },
  { path: "inbox", element: <InboxPage /> },
  { path: "connection", element: <ConnectionPage /> },
  { path: "domains", element: <DomainsPage /> },
  { path: "senders", element: <SendersPage /> },
  { path: "contacts", element: <ContactsPage /> },
  { path: "lists", element: listsGate(<ContactListsPage />) },
  { path: "lists/:listId", element: listsGate(<ContactListMembersPage />) },
  { path: "suppressions", element: <SuppressionsPage /> },
  { path: "campaigns", element: campaignsGate(<CampaignsPage />) },
  { path: "campaigns/new", element: campaignsGate(<NewCampaignPage />) },
  { path: "campaigns/:campaignId", element: campaignsGate(<CampaignPage />) },
  { path: "whatsapp/connection", element: <WhatsAppConnectionPage /> },
  { path: "*", element: <NotFoundPage /> },
];
