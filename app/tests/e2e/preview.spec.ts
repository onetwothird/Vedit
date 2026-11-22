import { expect, test } from "@playwright/test";

const clockSeconds = (s: string): number => {
  const m = /^(\d+):(\d+\.\d)/.exec(s.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
};

test.beforeEach(async ({ page }) => {
  await page.goto("/#/p/mock-1/pick");
  await expect(page.getByRole("heading", { name: "12 clips found · 8 kept" })).toBeVisible();
  await expect(page.locator(".pv video")).toBeVisible();
  await expect.poll(() => page.locator(".pv video").evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
});

test("plays: the timecode advances and crossing a cut jumps the video ahead", async ({ page }) => {
  // sample currentTime every 20 ms; a play-through that stays in one range never moves more than a frame or two
  await page.evaluate(() => {
    const v = document.querySelector("video")!;
    const w = window as unknown as { __jumps: number[] };
    w.__jumps = [];
    let last = v.currentTime;
    setInterval(() => {
      const d = v.currentTime - last;
      if (Math.abs(d) > 0.25) w.__jumps.push(d);
      last = v.currentTime;
    }, 20);
  });
  const tc = page.locator(".pv-tc");
  const before = clockSeconds((await tc.textContent())!);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(2000);
  const after = clockSeconds((await tc.textContent())!);
  expect(after).toBeGreaterThan(before + 1);
  const jumps = await page.evaluate(() => (window as unknown as { __jumps: number[] }).__jumps);
  expect(jumps.length).toBeGreaterThan(0);
  expect(jumps[0]).toBeGreaterThan(0.25); // forward over the cut "um"
});

test("Space toggles play and pause, and the arrows skip 5 seconds", async ({ page }) => {
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
  const tc = page.locator(".pv-tc");
  const t0 = clockSeconds((await tc.textContent())!);
  await page.keyboard.press("ArrowRight");
  await expect.poll(async () => clockSeconds((await tc.textContent())!)).toBeCloseTo(t0 + 5, 0);
  await page.keyboard.press("ArrowLeft");
  await expect.poll(async () => clockSeconds((await tc.textContent())!)).toBeCloseTo(t0, 0);
});

test("safe zone guide is off by default, toggles, and is remembered", async ({ page }) => {
  await expect(page.locator(".pv-safe")).toHaveCount(0);
  await page.getByRole("button", { name: "Safe zone" }).click();
  await expect(page.locator(".pv-safe")).toHaveCount(1);
  expect(await page.evaluate(() => localStorage.getItem("vedit.safezone"))).toBe("1");
  await page.reload();
  await expect(page.locator(".pv-safe")).toHaveCount(1);
});

test("a captions group shows while playing", async ({ page }) => {
  await page.keyboard.press("Space");
  await expect(page.locator("[data-caption]")).toBeVisible();
});
