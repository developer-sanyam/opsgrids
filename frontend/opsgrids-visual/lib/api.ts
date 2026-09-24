/**
 * The API an app page talks to during a run: route interception answering from
 * `fixtures/api/**`, with nothing reaching a server.
 *
 * Every route names the OpenAPI operation its payload stands in for, and
 * `fixtures/contracts.test.ts` validates every payload against that operation's response schema
 * in `libs/contracts/openapi/`. A mock that drifts from what the service really returns fails the
 * harness's own unit tests before it can produce a misleading screenshot.
 *
 * A request no route answers is refused with a 501 and recorded, and the page test fails on it:
 * an unmocked call is a screen state the baseline does not control.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import { HARNESS_ROOT } from "./env";

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** Which contract a fixture answers for: `libs/contracts/openapi/<spec>.json`, one operation. */
export interface ContractRef {
  spec: string;
  /** The OpenAPI path, templated as the spec writes it (`/api/v1/deals/{deal_id}`). */
  path: string;
  method?: Lowercase<Method> | undefined;
  status?: string | undefined;
}

export interface MockRoute {
  method: Method;
  /** The request's pathname, exactly. */
  path: string;
  /** Query parameters that must be present with these values. Others are ignored. */
  query?: Record<string, string> | undefined;
  /** A JSON file under `fixtures/api/`. */
  fixture: string;
  contract: ContractRef;
  status?: number | undefined;
}

export const FIXTURE_ROOT = join(HARNESS_ROOT, "fixtures", "api");

export function readFixture(file: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURE_ROOT, file), "utf8"));
}

/** The most specific route for a request: every `query` entry must match; more entries wins. */
export function match(routes: readonly MockRoute[], method: string, url: URL): MockRoute | undefined {
  return routes
    .filter(
      (route) =>
        route.method === method &&
        route.path === url.pathname &&
        Object.entries(route.query ?? {}).every(([key, value]) => url.searchParams.get(key) === value),
    )
    .sort((a, b) => Object.keys(b.query ?? {}).length - Object.keys(a.query ?? {}).length)[0];
}

export interface MockedApi {
  /** Requests no route answered. */
  unmocked: string[];
}

/** Answer `origin/api/**` from `routes`. Registered after `isolate`, so it is consulted first. */
export async function mockApi(page: Page, origin: string, routes: readonly MockRoute[]): Promise<MockedApi> {
  const unmocked: string[] = [];
  await page.context().route(`${origin}/api/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const hit = match(routes, request.method(), url);
    if (!hit) {
      unmocked.push(`${request.method()} ${url.pathname}${url.search}`);
      await route.fulfill({
        status: 501,
        contentType: "application/problem+json",
        body: JSON.stringify({
          type: "about:blank",
          title: "Not mocked",
          status: 501,
          detail: `The visual harness has no fixture for ${request.method()} ${url.pathname}.`,
        }),
      });
      return;
    }
    await route.fulfill({
      status: hit.status ?? 200,
      contentType: "application/json",
      body: JSON.stringify(readFixture(hit.fixture)),
    });
  });
  return { unmocked };
}
