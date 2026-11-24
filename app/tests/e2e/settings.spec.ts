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
  const row = page.getByRole("listitem").filter({ hasText: /Large v3(?! Turbo)/ });
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

test("the caption preview shows each caption style", async ({ page }) => {
  await page.goto("/#/settings");
  const preview = page.locator(".cap-preview");
  await expect(preview).toHaveAttribute("data-style", "pop");
  for (const name of ["Box", "Clean", "Karaoke", "Emoji"]) {
    await preview.getByRole("radio", { name }).check();
    await expect(preview).toHaveAttribute("data-style", name.toLowerCase());
  }
  await expect(preview.locator("[data-emoji]")).toHaveText(/🔥/);
  await preview.getByRole("radio", { name: "Karaoke" }).check();
  await expect(preview.locator("[data-fill]").nth(1)).toHaveText("galing");
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

test("the caption preview follows the brand kit as you edit", async ({ page }) => {
  await page.goto("/#/settings");
  await page.getByLabel("Caption font").fill("Poppins");
  await expect(page.locator(".cap-preview [data-caption]")).toHaveCSS("font-family", /Poppins/);
  await page.getByLabel("Caption color").fill("#ff0000");
  await expect(page.locator(".cap-preview [data-caption]")).toHaveCSS("color", "rgb(255, 0, 0)");
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

/** Scrolls the app's content area so the section's top sits `frac` of the way down it (or to the very end). */
async function scrollSection(page: import("@playwright/test").Page, id: string, frac: number | "end") {
  await page.evaluate(({ id, frac }) => {
    const el = document.getElementById(id)!;
    let box = el.parentElement;
    while (box && !(box.scrollHeight > box.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
    const scroller = box ?? document.scrollingElement!;
    const top = box ? box.getBoundingClientRect().top : 0;
    scroller.scrollTop = frac === "end" ? scroller.scrollHeight : scroller.scrollTop + el.getBoundingClientRect().top - top - (innerHeight - top) * frac;
  }, { id, frac });
}

test("the section list follows the scroll, all the way to Brand kit and Updates", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#/settings");
  const nav = page.getByRole("navigation", { name: "Settings sections" });
  const current = nav.locator('[aria-current="location"]');
  await expect(current).toHaveText("Account");
  // each section's heading at the top of the page marks it; Brand kit can't scroll that far, so it is a little lower
  for (const [id, name, frac] of [["general", "General", 0], ["ai", "AI clip picking", 0], ["models", "Language models", 0], ["brand", "Brand kit", 0.2]] as const) {
    await scrollSection(page, id, frac);
    await expect(current).toHaveText(name);
  }
  await scrollSection(page, "updates", "end");
  await expect(current).toHaveText("Updates");
  await scrollSection(page, "account", 0);
  await expect(current).toHaveText("Account");

  // a click marks the chosen section even when the page can't scroll it to the top
  await nav.getByRole("button", { name: "Brand kit" }).click();
  await expect(current).toHaveText("Brand kit");
  await expect(page.locator("#brand")).toBeInViewport();
});

test("the section list lines up with the title and the first heading", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#/settings");
  const title = await page.getByRole("heading", { name: "Settings", level: 1 }).boundingBox();
  const icon = await page.getByRole("navigation", { name: "Settings sections" }).locator("button svg").first().boundingBox();
  const first = await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button").first().boundingBox();
  const heading = await page.getByRole("heading", { name: "Account", level: 2 }).boundingBox();
  expect(Math.abs(icon!.x - title!.x)).toBeLessThanOrEqual(2);
  expect(Math.abs((first!.y + first!.height / 2) - (heading!.y + heading!.height / 2))).toBeLessThanOrEqual(2);
});
