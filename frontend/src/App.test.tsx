import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router";

import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { installFetchMock, jsonResponse } from "./test/mockFetch";

afterEach(cleanup);

/**
 * The version footer (`src/components/Footer.tsx`) is rendered as a sibling of `<Routes>` in
 * `App.tsx`, deliberately outside `<Layout>`'s `<RequireAuth>` subtree — it must show on every
 * route, including ones with no session. `/api/auth/me` answering 401 is the normal "not logged
 * in" state (`AuthContext.tsx`), not a test error condition.
 */
function renderAt(path: string): void {
  installFetchMock({
    "/api/auth/me": () => jsonResponse(401, { detail: "Nicht angemeldet." }),
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("App footer", () => {
  it("shows the app version on the login page", async () => {
    renderAt("/login");
    await waitFor(() => expect(screen.getByText(/^Version /)).not.toBeNull());
  });

  it("shows the app version on an unknown route (404 page)", async () => {
    renderAt("/nicht-vorhanden");
    await waitFor(() => expect(screen.getByText(/^Version /)).not.toBeNull());
  });
});
