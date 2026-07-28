import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthProvider, RequireAdmin, RequireAuth } from "./AuthContext";
import { installFetchMock, jsonResponse } from "../test/mockFetch";

function renderGuarded(meResponse: () => Response, initialPath = "/geschuetzt") {
  const mock = installFetchMock({ "/api/auth/me": meResponse });
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<p>Login-Seite</p>} />
          <Route
            path="/geschuetzt"
            element={
              <RequireAuth>
                <p>Geheime Klausurdaten</p>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAdmin>
                <p>Benutzerverwaltung</p>
              </RequireAdmin>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
  return mock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RequireAuth", () => {
  it("redirects to /login when /api/auth/me answers 401", async () => {
    renderGuarded(() => jsonResponse(401, { detail: "Nicht angemeldet." }));

    expect(await screen.findByText("Login-Seite")).not.toBeNull();
    expect(screen.queryByText("Geheime Klausurdaten")).toBeNull();
  });

  it("does not surface the 401 as an error: it is the normal logged-out answer", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    renderGuarded(() => jsonResponse(401, { detail: "Nicht angemeldet." }));

    await screen.findByText("Login-Seite");

    expect(screen.queryByRole("alert")).toBeNull();
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("renders the protected content once a session exists", async () => {
    renderGuarded(() => jsonResponse(200, { id: 1, username: "dozentin", is_admin: false }));

    expect(await screen.findByText("Geheime Klausurdaten")).not.toBeNull();
    expect(screen.queryByText("Login-Seite")).toBeNull();
  });
});

describe("RequireAdmin", () => {
  it("redirects an anonymous visitor to /login", async () => {
    renderGuarded(() => jsonResponse(401, { detail: "Nicht angemeldet." }), "/admin");

    expect(await screen.findByText("Login-Seite")).not.toBeNull();
  });

  it("refuses a logged-in non-admin", async () => {
    renderGuarded(
      () => jsonResponse(200, { id: 1, username: "dozentin", is_admin: false }),
      "/admin",
    );

    expect(await screen.findByText("Kein Zugriff")).not.toBeNull();
    expect(screen.queryByText("Benutzerverwaltung")).toBeNull();
  });

  it("lets an admin through", async () => {
    renderGuarded(
      () => jsonResponse(200, { id: 2, username: "admin", is_admin: true }),
      "/admin",
    );

    expect(await screen.findByText("Benutzerverwaltung")).not.toBeNull();
  });
});
