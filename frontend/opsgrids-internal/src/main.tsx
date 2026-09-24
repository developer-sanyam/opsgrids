import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "@opsgrids/ui/fonts";
import "./fonts.css";
import "./index.css";
import { OPERATOR_SESSION_KEY, OPERATOR_THEME_KEY } from "./auth/keys";
import { routes } from "./router";

const router = createBrowserRouter(routes);

// Name this app's session slot before anything can read a token. The operator's is its own — never
// a tenant app's — so the two token populations cannot read each other's storage.
configureSession(OPERATOR_SESSION_KEY);

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey={OPERATOR_THEME_KEY}>
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
