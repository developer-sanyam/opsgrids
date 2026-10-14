import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useCan } from "../permissions/context";
import { useAuth } from "./context";
import { ProductAuthProvider } from "./ProductAuthProvider";
import type { ProductFixture } from "./ProductAuthProvider";
import { RequireAuth } from "./RequireAuth";
import type { MeOut } from "./types";

const ORG = { organization_id: "org-1", slug: "acme", name: "Acme", roles: ["admin"], role_names: ["Admin"] };

const USER: MeOut = {
  user_id: "u-1",
  email: "sofia@acme.example.com",
  full_name: "Sofia Marchetti",
  organization_id: ORG.organization_id,
  membership_id: "m-1",
  roles: ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [ORG],
};

const FIXTURE: ProductFixture = { user: USER, grants: ["crm.pipeline.deal.write:ORG"] };

function Probe() {
  const { user } = useAuth();
  const canWrite = useCan("crm.pipeline.deal.write", "ORG");
  const canDelete = useCan("crm.pipeline.deal.delete", "ORG");
  return (
    <p>
      {user?.full_name} {canWrite ? "can write" : "cannot write"} {canDelete ? "can delete" : "cannot delete"}
    </p>
  );
}

describe("ProductAuthProvider", () => {
  it("signs a fixture identity in with exactly the fixture's grants", async () => {
    render(
      <MemoryRouter>
        <ProductAuthProvider fixture={FIXTURE} mark={<span>mark</span>}>
          <RequireAuth>
            <Probe />
          </RequireAuth>
        </ProductAuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Sofia Marchetti can write cannot delete")).toBeTruthy();
  });

  it("holds the boot screen with the app's own mark and words until the session is known", () => {
    render(
      <MemoryRouter>
        <ProductAuthProvider mark={<span>product mark</span>} bootMessage="Opening the service desk…">
          <RequireAuth>
            <Probe />
          </RequireAuth>
        </ProductAuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText("product mark")).toBeTruthy();
    expect(screen.getByText("Opening the service desk…")).toBeTruthy();
  });

  it("falls back to the default words when the app names none", () => {
    render(
      <MemoryRouter>
        <ProductAuthProvider mark={<span>product mark</span>}>
          <RequireAuth>
            <Probe />
          </RequireAuth>
        </ProductAuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText("Signing you in…")).toBeTruthy();
  });

  it("lets a RequireAuth prop override what the provider declared", () => {
    render(
      <MemoryRouter>
        <ProductAuthProvider mark={<span>provider mark</span>}>
          <RequireAuth logo={<span>own mark</span>} message="Custom">
            <Probe />
          </RequireAuth>
        </ProductAuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText("own mark")).toBeTruthy();
    expect(screen.getByText("Custom")).toBeTruthy();
  });
});
