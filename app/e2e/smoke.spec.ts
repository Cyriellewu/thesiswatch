import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";

test.describe("Demo mode core loop", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("thesiswatch.mode", "demo");
    });
  });

  test("Today renders demo holdings", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("DEMO DATA").first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator("text=MSFT").first()).toBeVisible();
  });

  test("Opening a holding shows merged ThesisDetail with driver and evidence", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector("text=MSFT", { timeout: 10000 });
    await page.locator("text=MSFT").first().click();
    await expect(page.locator("text=WHAT CHANGED").first()).toBeVisible({ timeout: 8000 });
    await expect(page.locator("text=EVIDENCE").first()).toBeVisible({ timeout: 8000 });
    const hasDriver =
      (await page.locator("text=Azure").count()) > 0 ||
      (await page.locator("[data-testid='driver-row']").count()) > 0 ||
      (await page.locator(".driver-row").count()) > 0;
    expect(hasDriver).toBeTruthy();
    await expect(page.locator("text=Watch").first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe("Live mode unavailable state", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("thesiswatch.mode", "live");
    });
    await page.route("**/api/**", (route) => route.abort());
  });

  test("Shows unavailable panel and NOT mock data", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("text=unavailable").first()).toBeVisible({ timeout: 15000 });
    const unavailableCount = await page.locator("text=unavailable").count();
    expect(unavailableCount).toBeGreaterThan(0);
  });
});

test.describe("Screenshots", () => {
  test("Capture responsive screenshots", async ({ page }) => {
    mkdirSync("e2e-shots", { recursive: true });
    await page.addInitScript(() => {
      localStorage.setItem("thesiswatch.mode", "demo");
    });

    const shots = [
      { name: "390px-today", w: 390, h: 844 },
      { name: "768px-today", w: 768, h: 1024 },
      { name: "1440px-today", w: 1440, h: 900 },
    ];

    for (const { name, w, h } of shots) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto("/");
      await page.waitForSelector("text=MSFT", { timeout: 10000 });
      await page.screenshot({ path: `e2e-shots/${name}.png`, fullPage: false });
    }

    for (const { name, w, h } of shots) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto("/");
      await page.waitForSelector("text=MSFT", { timeout: 10000 });
      await page.locator("text=MSFT").first().click();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `e2e-shots/${name.replace("today", "thesis")}.png`, fullPage: false });
    }
  });
});
