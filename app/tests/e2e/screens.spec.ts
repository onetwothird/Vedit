// Screenshots for the visual check against the mockup. Run with SCREENSHOTS=<dir> npx playwright test screens.
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const dir = process.env.SCREENSHOTS;
test.skip(!dir, "set SCREENSHOTS to a folder to save screenshots");

const shot = (page: Page, name: string) => page.screenshot({ path: path.join(dir!, `t15-${name}.png`) });
const file = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("x") });

test("screenshots", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/#/");
  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.reload();
  await expect(page.getByRole("link", { name: /podcast-ep12/ })).toBeVisible();
  await shot(page, "home-dark");

  await page.evaluate(() => localStorage.setItem("vedit.theme", "light"));
  await page.reload();
  await expect(page.getByRole("link", { name: /podcast-ep12/ })).toBeVisible();
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await shot(page, "home-light");

  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.goto("/#/p/new/import");
  await page.reload();
  await shot(page, "import-empty-dark");
  await page.locator('input[type="file"]').setInputFiles(file("podcast_ep13.mp4"));
  await shot(page, "import-idle");

  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("Listening to the audio")).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "import-analyzing");

  await page.goto("/#/p/new/import");
  await page.reload();
  await page.locator('input[type="file"]').setInputFiles(file("will-fail.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await shot(page, "import-failed");
});
