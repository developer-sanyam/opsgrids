import { StrictMode } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { ThemeProvider } from "@opsgrids/ui";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { RequireGrant } from "./auth/RequireGrant";
import { CampaignPage, NewCampaignPage } from "./routes/CampaignEditor";
import { CampaignsPage } from "./routes/Campaigns";
import { ConnectionPage } from "./routes/Connection";
import { ContactListMembersPage } from "./routes/ContactListMembers";
import { ContactListsPage } from "./routes/ContactLists";
import { ContactsPage } from "./routes/Contacts";
import { DomainsPage } from "./routes/Domains";
import { InboxPage } from "./routes/Inbox";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { OverviewPage } from "./routes/Overview";
import { SendersPage } from "./routes/Senders";
import { SuppressionsPage } from "./routes/Suppressions";
import { WhatsAppConnectionPage } from "./routes/WhatsAppConnection";
import { WhatsAppTemplatesPage } from "./routes/WhatsAppTemplates";
import { LogoMark } from "./ui/LogoMark";

// ADR-0003 addendum: sign-in only ever happens on the dashboard, the one host the refresh
// cookie lives on. Set in production builds only (`publish-web.sh`); unset in dev, where this
// app's own `/login` form still works the same as it always has, same-origin.
const AUTH_ORIGIN = import.meta.env.VITE_AUTH_ORIGIN as string | undefined;
if (AUTH_ORIGIN) configureAuthOrigin(AUTH_ORIGIN);

/**
 * The screens: the six sending-identity screens Phase 1 shipped with, the audience — contacts,
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

const router = createBrowserRouter([
  {
    // The auth provider sits inside the router so it can use navigation and the location.
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    children: [
      {
        path: "/login",
        element: AUTH_ORIGIN ? (
          <RedirectToCentralLogin authOrigin={AUTH_ORIGIN} logo={<LogoMark className="size-11" />} />
        ) : (
          <LoginPage />
        ),
      },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <OverviewPage /> },
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
          { path: "whatsapp/templates", element: <WhatsAppTemplatesPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.engage.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.engage.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
