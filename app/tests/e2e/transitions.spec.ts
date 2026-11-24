import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function open(page: Page) {
  await page.goto("/#/p/mock-1/edit/h01");
  await expect(page.locator('.tx [data-w="0"]')).toBeVisible();
  await page.getByRole("tab", { name: "Transition" }).click();
}

test("pick a transition between cuts, change its length, undo, and find it saved", async ({ page }) => {
  await open(page);
  const cut = page.getByRole("radio", { name: "Cut", exact: true });
  await expect(cut).toBeChecked();
  await expect(page.getByRole("radio", { name: "0.4 s" })).toHaveCount(0);

  await page.getByRole("radio", { name: "Crossfade" }).check();
  await expect(page.getByRole("radio", { name: "0.4 s" })).toBeChecked();
  await page.getByRole("radio", { name: "0.6 s" }).check();
  await expect(page.getByRole("radio", { name: "0.6 s" })).toBeChecked();
  await page.getByRole("radio", { name: "Whip" }).check();
  await expect(page.getByRole("radio", { name: "Whip" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "0.6 s" })).toBeChecked(); // the length stays
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();

  await page.keyboard.press("Control+z");
  await expect(page.getByRole("radio", { name: "Crossfade" })).toBeChecked();

  await page.reload();
  await open(page);
  await expect(page.getByRole("radio", { name: "Crossfade" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "0.6 s" })).toBeChecked();
  await page.getByRole("radio", { name: "Cut", exact: true }).check();
  await expect(page.getByRole("radio", { name: "0.6 s" })).toHaveCount(0);
});

test("the Transition tool has no serious accessibility problems", async ({ page }) => {
  await open(page);
  await page.getByRole("radio", { name: "Whip" }).check();
  const res = await new AxeBuilder({ page }).analyze();
  const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
