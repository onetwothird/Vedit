import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("the AI key is saved through the key store and never reaches browser storage", async ({ page }) => {
  await page.goto("/#/settings");
  const secret = "sk-test-" + "k".repeat(12);

  const field = page.getByLabel("API key");
  await expect(field).toHaveAttribute("type", "password");
  await field.fill(secret);
  const show = page.getByRole("button", { name: "Show" });
  await show.click();
  await expect(page.getByRole("button", { name: "Hide" })).toHaveAttribute("aria-pressed", "true");
  await expect(field).toHaveAttribute("type", "text");

  await page.getByRole("button", { name: "Save key" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Key saved on this computer." })).toBeVisible();
  await expect(field).toHaveValue("");

  // the mock key store stands in for set_ai_key; the key is there and nowhere else
  expect(await page.evaluate(() => [...(window as unknown as { __veditMockKeys: Map<string, string> }).__veditMockKeys.values()]))
    .toEqual([secret]);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(stored).not.toContain(secret);

  await page.getByRole("button", { name: "Test key" }).click();
  await expect(page.getByText("The key works.")).toBeVisible();

  await page.getByRole("button", { name: "Remove key" }).click();
  await expect(page.getByText("Key removed.")).toBeVisible();
});

test("a bad key says the provider did not accept it", async ({ page }) => {
  await page.goto("/#/settings");
  await page.getByLabel("API key").fill("nope");
  await page.getByRole("button", { name: "Test key" }).click();
  await expect(page.getByText("The provider did not accept this key.")).toBeVisible();
});

test("downloading a model shows progress, then Installed", async ({ page }) => {
  await page.goto("/#/settings");
  const row = page.getByRole("listitem").filter({ hasText: "Large v3" });
  await expect(row).toContainText("3.1 GB");
  await expect(row).toContainText("Not installed");
  await row.getByRole("button", { name: "Download" }).click();
  await expect(row.getByRole("progressbar")).toBeVisible();
  await expect(row.getByText("Installed", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: "Delete" })).toBeVisible();

  await row.getByRole("button", { name: "Delete" }).click();
  await expect(row.getByText("Not installed")).toBeVisible();
});

test("the theme control applies at once and stays in step with the header button", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.goto("/#/settings");
  await page.reload();

  await page.getByRole("radio", { name: "Light" }).check({ force: true });
  await expect(page.locator("html")).toHaveClass(/light/);
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();

  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).not.toHaveClass(/light/);
  await expect(page.getByRole("radio", { name: "Dark" })).toBeChecked();
});

test("language, platform and provider defaults are kept where Import reads them", async ({ page }) => {
  await page.goto("/#/settings");
  await page.getByRole("radio", { name: "Taglish" }).check({ force: true });
  await page.getByRole("radio", { name: "TikTok" }).check({ force: true });
  await page.getByRole("radio", { name: "OpenAI" }).check({ force: true });
  const kept = await page.evaluate(() => [localStorage.getItem("vedit.language"), localStorage.getItem("vedit.platform"), localStorage.getItem("vedit.ai.provider")]);
  expect(kept).toEqual(["taglish", "tiktok", "openai"]);

  await page.goto("/#/p/new/import");
  await expect(page.getByRole("radio", { name: "Taglish" })).toBeChecked();
});

test("the brand kit saves and comes back", async ({ page }) => {
  await page.goto("/#/settings");
  await page.getByLabel("Caption font").fill("Poppins");
  await page.locator('input[type="file"][aria-labelledby$="-l"]').first().setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: Buffer.from("x") });
  await expect(page.getByText("logo.png")).toBeVisible();
  await page.getByRole("button", { name: "Save brand kit" }).click();
  await expect(page.getByText("Brand kit saved.")).toBeVisible();
});

test("updates show the version and wait for the installed app", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByText(/^Version \d+\.\d+\.\d+/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeDisabled();
  await expect(page.getByText("Updates come with the installed app.")).toBeVisible();
});

test("the account section offers Sign in when nobody is signed in", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
});

test("fits a 375px phone without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/#/settings");
  await expect(page.getByRole("heading", { name: "Brand kit" })).toBeVisible();
  const over = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth
    || [...document.querySelectorAll(".screen")].some((e) => e.scrollWidth > e.clientWidth));
  expect(over).toBe(false);
});

test("section tabs: a click scrolls to the section and marks it current; scrolling moves it", async ({ page }) => {
  await page.goto("/#/settings");
  const tabs = page.getByRole("navigation", { name: "Settings sections" });
  await expect(tabs.getByRole("link", { name: "Account" })).toHaveAttribute("aria-current", "true");

  await tabs.getByRole("link", { name: "Brand kit" }).click();
  await expect(tabs.getByRole("link", { name: "Brand kit" })).toHaveAttribute("aria-current", "true");
  await expect(tabs.getByRole("link", { name: "Account" })).not.toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("heading", { name: "Brand kit" })).toBeInViewport();
  await expect(page.locator("#brand")).toBeFocused();

  // scrolling back to the top makes Account current again
  await page.locator(".screen").evaluate((el) => el.scrollTo({ top: 0 }));
  await expect(tabs.getByRole("link", { name: "Account" })).toHaveAttribute("aria-current", "true");
});

test("the caption preview follows the brand kit as you edit", async ({ page }) => {
  await page.goto("/#/settings");
  await page.getByLabel("Caption font").fill("Poppins");
  await expect(page.locator(".cap-line")).toHaveCSS("font-family", /Poppins/);
  await page.getByLabel("Caption color").fill("#ff0000");
  await expect(page.locator(".cap-line")).toHaveCSS("color", "rgb(255, 0, 0)");
});

for (const scheme of ["dark", "light"] as const) {
  test(`no serious accessibility problems in ${scheme} theme`, async ({ page }) => {
    await page.goto("/");
    await page.evaluate((v) => localStorage.setItem("vedit.theme", v), scheme);
    await page.goto("/#/settings");
    await page.reload();
    await expect(page.getByRole("heading", { name: "Brand kit" })).toBeVisible();
    const res = await new AxeBuilder({ page }).analyze();
    const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
}
