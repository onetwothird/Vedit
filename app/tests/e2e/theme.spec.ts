import { expect, test } from "@playwright/test";

test("the header's own light/dark button switches the theme and remembers it", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.reload();

  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect(page.locator("html")).toHaveClass(/light/);
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();

  await page.reload();
  await expect(page.locator("html")).toHaveClass(/light/);
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).not.toHaveClass(/light/);

  // the account menu no longer carries the switch
  await page.getByRole("button", { name: "Account and settings" }).click();
  await expect(page.getByRole("radio", { name: "Light" })).toHaveCount(0);
});
