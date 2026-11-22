import { expect, test } from "@playwright/test";

const file = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("x") });


test("ranked list, keep toggle, sort and keyboard selection", async ({ page }) => {
  await page.goto("/#/p/mock-1/pick");
  await expect(page.getByRole("heading", { name: "12 clips found · 8 kept" })).toBeVisible();
  const titles = page.locator(".clip-title");
  await expect(titles.first()).toHaveText("Nobody tells you this when you start");

  await page.getByRole("checkbox", { name: /Keep Long story about my laptop/ }).check({ force: true });
  await expect(page.getByRole("heading", { name: "12 clips found · 9 kept" })).toBeVisible();

  await page.getByRole("radio", { name: "In order" }).check();
  await expect(page.getByRole("radio", { name: "In order" })).toBeChecked();
  await page.getByRole("radio", { name: "Best first" }).check();

  await page.locator(".clip-main").nth(2).click();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("[aria-current=true] .clip-title")).toHaveText("My boss laughed at my portfolio");
  await page.keyboard.press("ArrowUp");
  await expect(page.locator("[aria-current=true] .clip-title")).toHaveText("Why I almost quit after a year");
  await page.getByRole("button", { name: "Edit selected" }).click();
  await expect(page).toHaveURL(/#\/p\/mock-1\/edit\/h03$/);
});

test("find more clips adds six", async ({ page }) => {
  await page.goto("/#/p/mock-1/pick");
  await expect(page.getByRole("heading", { name: "12 clips found · 8 kept" })).toBeVisible();
  const more = page.getByRole("button", { name: /Find more clips|Finding more clips/ });
  await more.click();
  await expect(page.getByRole("button", { name: /Finding more clips/ })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "18 clips found · 14 kept" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Added 6 clips.")).toBeVisible();
});

test("a project that is analyzed from import lands on pick with clips", async ({ page }) => {
  await page.goto("/#/p/new/import");
  await page.locator('input[type="file"]').setInputFiles(file("talk.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("heading", { name: /clips found/ })).toBeVisible({ timeout: 20_000 });
  await expect(page).toHaveURL(/\/pick$/);
});

test("a video with no speech shows the empty state and goes back home", async ({ page }) => {
  await page.goto("/#/p/new/import");
  await page.locator('input[type="file"]').setInputFiles(file("silent.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("No speech found in this video. vedit finds clips from what people say.")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("link", { name: "Choose another video" }).click();
  await expect(page).toHaveURL(/#\/$/);
});
