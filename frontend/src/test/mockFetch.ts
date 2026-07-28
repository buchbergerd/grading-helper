import { vi } from "vitest";

/**
 * Minimal stand-in for a `fetch` Response — only the three members `src/api/client.ts` uses.
 * The body is handed over as raw text so the test controls the exact JSON string, which is
 * what makes it possible to prove that a Decimal arrives as the string `"12.50"` (a real
 * `Response.json()` would be equally faithful, but this keeps the fixture explicit).
 */
export function jsonResponse(status: number, body: unknown): Response {
  const text = body === undefined ? "" : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

export type RouteHandler = (url: string, init: RequestInit | undefined) => Response;

/**
 * Installs a fake `fetch` that dispatches on the request path. Returns the mock so tests can
 * assert on call arguments (e.g. that `credentials: "same-origin"` was sent).
 */
export function installFetchMock(routes: Record<string, RouteHandler>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.split("?")[0] ?? url;
    const handler = routes[path];
    if (handler === undefined) {
      throw new Error(`Unerwarteter Request im Test: ${url}`);
    }
    return Promise.resolve(handler(url, init));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}
