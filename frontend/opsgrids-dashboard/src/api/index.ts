/**
 * Every call the dashboard makes, in one place, typed against `./types`. All go through
 * `@opsgrids/client`'s `apiRequest`: same-origin `/api/v1`, bearer attached from the session,
 * RFC 7807 problems raised as `ApiError`.
 */
import { apiRequest } from "@opsgrids/client";

import type {
  Activation,
  Invoice,
  Plan,
  Product,
  SignupRequest,
  SignupResponse,
  Subscription,
} from "./types";

// ------------------------------------------------------------------ before there is a session

/** `POST /account/signup` — public, no bearer. */
export function signup(payload: SignupRequest): Promise<SignupResponse> {
  return apiRequest<SignupResponse>("/account/signup", {
    method: "POST",
    body: payload,
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/password/forgot` — public; always 202, whether or not the address exists. */
export function requestPasswordLink(email: string): Promise<void> {
  return apiRequest<void>("/auth/password/forgot", {
    method: "POST",
    body: { email },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/password/reset` — public; the token is the credential. */
export function setPasswordFromLink(token: string, newPassword: string): Promise<void> {
  return apiRequest<void>("/auth/password/reset", {
    method: "POST",
    body: { token, new_password: newPassword },
    auth: false,
    signalUnauthorized: false,
  });
}

// ------------------------------------------------------------------ the marketplace

/** `GET /products` — the catalogue this deployment sells. */
export function listProducts(): Promise<Product[]> {
  return apiRequest<Product[]>("/products");
}

/** `GET /products/activations` — what this organisation may use today. */
export function listActivations(): Promise<Activation[]> {
  return apiRequest<Activation[]>("/products/activations");
}

/** `GET /plans?product_key=…` — the published plans a customer may be sold for one product. */
export function listPlans(productKey: string): Promise<Plan[]> {
  return apiRequest<Plan[]>(`/plans?product_key=${encodeURIComponent(productKey)}`);
}

// ------------------------------------------------------------------ money

/** `GET /subscriptions` — this organisation's subscriptions. */
export function listSubscriptions(): Promise<Subscription[]> {
  return apiRequest<Subscription[]>("/subscriptions");
}

/**
 * `POST /subscriptions` — "Start a 14-day trial". This is what a marketplace button does
 * (ADR-0013 §1): the subscription is the thing bought, the activation follows it by event.
 */
export function startTrial(productKey: string, planVersionId: string, trialDays = 14): Promise<Subscription> {
  return apiRequest<Subscription>("/subscriptions", {
    method: "POST",
    body: { product_key: productKey, plan_version_id: planVersionId, trial_days: trialDays },
  });
}

/** `GET /invoices` — frozen invoices, newest first as the service returns them. */
export function listInvoices(): Promise<Invoice[]> {
  return apiRequest<Invoice[]>("/invoices");
}
