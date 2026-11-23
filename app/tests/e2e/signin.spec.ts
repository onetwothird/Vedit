import { expect, test } from "@playwright/test";

// A fresh install: nobody signed in and "Skip for now" not chosen yet.
test.use({ storageState: { cookies: [], origins: [] } });

test("first run opens sign in, and Skip for now goes to Home and stays skipped", async ({ page }) => {
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "Sign in to vedit" })).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();
});

test("Continue with Google signs in and the header menu shows the account, then Sign out", async ({ page }) => {
  await page.goto("/#/signin");
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();
  await page.getByRole("button", { name: "Settings and appearance" }).click();
  await expect(page.getByText("Demo Creator")).toBeVisible();
  await expect(page.getByText("Free plan")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to vedit" })).toBeVisible();
});

test("email: create an account, sign out, sign back in; errors explain what to fix", async ({ page }) => {
  await page.goto("/#/signin");
  await page.getByRole("button", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create your vedit account" })).toBeVisible();
  await page.getByLabel("Name").fill("Ana Reyes");
  await page.getByLabel("Email").fill("ana@example");
  await page.getByLabel("Password").fill("short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("alert")).toHaveText(/name@example.com/);
  await page.getByLabel("Email").fill("ana@example.com");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("alert")).toHaveText(/at least 8 characters/);
  await page.getByLabel("Password").fill("test-pass-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();

  await page.getByRole("button", { name: "Settings and appearance" }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("test-pass-123");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(/Create one instead/);
  await page.getByLabel("Email").fill("ana@example.com");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();
});

test("sign in fits a phone screen without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/#/signin");
  await expect(page.getByRole("heading", { name: "Sign in to vedit" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth)).toBe(true);
});
