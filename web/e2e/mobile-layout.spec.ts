import { expect, test, type Page } from "@playwright/test";

const bootstrap = {
  auth: { surface: "local_admin", mode: "none", csrf_header: "x-csrf-token" },
  development: false,
  schema_version: 1,
  workspace_dir: "/tmp/workspaces",
  actors: [
    {
      id: "amy",
      name: "Amy",
      description: "Mobile layout fixture",
      enabled: true,
      status: "ready",
      last_error: null,
      workspace: "amy",
      model: { type: "exact", endpoint_id: "primary", model: "test-model" },
      context_compression_tokens: 8000,
      max_loaded_skills_warning: 20,
      loaded_skill_count: 2,
      workspace_skill_count: 2,
      loaded_skills_warning: false,
    },
  ],
  integrations: [],
  routes: [],
};

async function mockApi(page: Page) {
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ contentType: "text/css", body: "" }));
  await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
  await page.route("**/healthz", (route) => route.fulfill({ json: { ok: true, status: "ok" } }));
  await page.route("http://127.0.0.1:5173/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/bootstrap") return route.fulfill({ json: bootstrap });
    if (path === "/api/conversations") return route.fulfill({ json: [] });
    if (path === "/api/skills") {
      return route.fulfill({
        json: {
          warning: "",
          items: [{
            id: "mobile-skill",
            name: "Mobile skill",
            description: "Responsive resource-list fixture",
            scope: "global",
            inspect_hint: "",
            source: "custom",
            can_edit: true,
            can_update: false,
            can_delete: true,
            can_copy: true,
            error: "",
          }],
        },
      });
    }
    if (path === "/api/credentials") {
      return route.fulfill({
        json: {
          items: [{
            id: "provider-token",
            label: "Provider token",
            kind: "api_key",
            provider: "example",
            owner_scope: "global",
            secret_ref: "secret://provider-token",
            redacted_summary: "configured",
            scopes: ["chat"],
            expires_at: null,
          }],
        },
      });
    }
    if (path === "/api/notifications/vapid-public-key") return route.fulfill({ json: { public_key: "" } });
    return route.fulfill({ json: { items: [] } });
  });
}

async function expectNoDocumentOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("shell remains contained across mobile, tablet, breakpoint, and desktop widths", async ({ page }) => {
  await mockApi(page);
  for (const viewport of [
    { width: 320, height: 568 },
    { width: 390, height: 844 },
    { width: 768, height: 1024 },
    { width: 861, height: 800 },
    { width: 1280, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/actors", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Actors" })).toBeVisible();
    await expectNoDocumentOverflow(page);
    if (viewport.width <= 860) {
      await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
    } else {
      await expect(page.getByRole("button", { name: "Open navigation" })).toBeHidden();
    }
  }
});

test("mobile navigation is modal and closes through every supported path", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/actors", { waitUntil: "domcontentloaded" });

  const menu = page.getByRole("button", { name: "Open navigation" });
  const drawer = page.getByRole("dialog", { name: "Navigation" });

  await menu.click();
  await expect(drawer).toBeVisible();
  await expect(page.locator("body")).toHaveAttribute("data-scroll-locked", /.+/);
  await page.keyboard.press("Tab");
  await expect.poll(() => page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true);
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(drawer).toBeHidden();

  await menu.click();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  await menu.click();
  await page.locator(".sidebar-drawer__overlay").click({ position: { x: 380, y: 420 }, force: true });
  await expect(drawer).toBeHidden();

  await menu.click();
  await drawer.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(drawer).toBeHidden();
  await expectNoDocumentOverflow(page);
});

test("mobile resource surfaces keep actions and metadata reachable", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto("/credentials", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Provider token")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open navigation" })).toHaveCSS("height", "44px");
  await expectNoDocumentOverflow(page);

  await page.goto("/skills", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Mobile skill")).toBeVisible();
  const idCell = page.locator('td[data-mobile-label="ID"]');
  await expect(idCell).toBeVisible();
  await expect(idCell).toHaveCSS("display", "grid");
  await expectNoDocumentOverflow(page);
});
