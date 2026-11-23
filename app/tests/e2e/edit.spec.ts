import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// mock-1 / h01 is the passage "Nobody um tells you this when you start. The first year is uh, like, brutal. I lost ..."
// with the two hard fillers (words 1 "um" and 12 "uh,") already cut.
const clockSeconds = (s: string): number => {
  const m = /^(\d+):(\d+\.\d)/.exec(s.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
};

const word = (page: Page, i: number) => page.locator(`.tx [data-w="${i}"]`);
const footer = (page: Page) => page.locator(".ed-cutinfo");

async function open(page: Page, hid = "h01") {
  await page.goto(`/#/p/mock-1/edit/${hid}`);
  await expect(word(page, 0)).toBeVisible();
  await expect(footer(page)).toHaveText(/2 words cut/);
  await expect(page.locator(".pv video")).toBeVisible();
  await expect.poll(() => page.locator(".pv video").evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
}

test("cut a word from the transcript, see the gap, and undo it", async ({ page }) => {
  await open(page);
  await expect(page.locator(".tl-gap")).toHaveCount(2);
  await word(page, 4).click(); // "this"
  await page.getByRole("button", { name: "Cut word" }).click();
  await expect(word(page, 4)).toHaveClass(/\bcut\b/);
  await expect(footer(page)).toHaveText(/3 words cut/);
  await expect(page.locator(".tl-gap")).toHaveCount(3);
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();

  await page.keyboard.press("Control+z");
  await expect(word(page, 4)).not.toHaveClass(/\bcut\b/);
  await expect(footer(page)).toHaveText(/2 words cut/);
  await expect(page.locator(".tl-gap")).toHaveCount(2);

  await page.keyboard.press("Control+Shift+z");
  await expect(word(page, 4)).toHaveClass(/\bcut\b/);
  await page.getByRole("button", { name: "Restore all" }).click();
  await expect(footer(page)).toHaveText(/0 words cut/);
  await expect(page.locator(".tl-gap")).toHaveCount(0);
});

test("a cut word offers Restore word", async ({ page }) => {
  await open(page);
  await word(page, 1).click(); // "um", cut by the first pass
  await page.getByRole("button", { name: "Restore word" }).click();
  await expect(word(page, 1)).not.toHaveClass(/\bcut\b/);
  await expect(footer(page)).toHaveText(/1 word cut/);
});

test("shift-click selects a range and Delete cuts all of it", async ({ page }) => {
  await open(page);
  await word(page, 5).click();
  await word(page, 7).click({ modifiers: ["Shift"] });
  await expect(page.locator(".tx .w.sel")).toHaveCount(3);
  await page.keyboard.press("Delete");
  for (const i of [5, 6, 7]) await expect(word(page, i)).toHaveClass(/\bcut\b/);
  await expect(footer(page)).toHaveText(/5 words cut/);
});

test("Fix spelling changes the caption on the video", async ({ page }) => {
  await open(page);
  await expect(page.locator("[data-caption]")).toContainText("Nobody");
  await word(page, 0).click();
  await page.getByRole("button", { name: "Fix spelling" }).click();
  const input = page.getByRole("textbox", { name: "Correct spelling" });
  await expect(input).toBeFocused();
  await input.fill("Noone");
  await input.press("Enter");
  await expect(page.locator("[data-caption]")).toContainText("Noone");
  await expect(word(page, 0)).toHaveText("Noone");

  // Esc leaves the word as it is
  await word(page, 0).click();
  await page.getByRole("button", { name: "Fix spelling" }).click();
  await page.getByRole("textbox", { name: "Correct spelling" }).fill("Zzz");
  await page.keyboard.press("Escape");
  await expect(word(page, 0)).toHaveText("Noone");
});

test("caption style, words at a time and position update the overlay", async ({ page }) => {
  await open(page);
  await page.getByRole("tab", { name: "Captions" }).click();
  await expect(page.locator("[data-box]")).toHaveCount(0);
  await page.getByRole("radio", { name: "Box" }).check();
  await expect(page.locator("[data-box]")).toBeVisible();
  await page.getByRole("radio", { name: "1", exact: true }).check();
  await expect(page.locator("[data-caption] [data-word]")).toHaveCount(1);
  const bottom = () => page.locator("[data-caption]").evaluate((e) => parseFloat((e as HTMLElement).style.bottom));
  const lower = await bottom();
  await page.getByRole("radio", { name: "Middle" }).check();
  await expect.poll(bottom).toBeGreaterThan(lower);
  await page.keyboard.press("Control+z");
  await expect.poll(bottom).toBeCloseTo(lower, 1);
});

test("framing and sound settings save to the clip", async ({ page }) => {
  await open(page);
  await page.getByRole("tab", { name: "Framing" }).click();
  await page.getByRole("radio", { name: "Blur" }).check();
  await expect(page.getByRole("radio", { name: "Blur" })).toBeChecked();
  await page.getByRole("tab", { name: "Sound" }).click();
  await page.getByRole("radio", { name: "Remove background noise: On" }).check();
  await expect(page.getByRole("radio", { name: "Remove background noise: On" })).toBeChecked();
  await page.locator('input[type="file"][accept*="audio"]').setInputFiles({ name: "lofi.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("x") });
  await expect(page.locator(".tl-music")).toContainText("lofi.mp3");
  await expect(page.getByText("Music and color are applied on export.")).toBeVisible();
  await page.getByRole("tab", { name: "Framing" }).click();
  await expect(page.getByRole("radio", { name: "Blur" })).toBeChecked();
});

test("dragging a clip edge in Trim mode snaps to a word boundary", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Trim", exact: true }).click();
  const handle = page.locator("[data-edge=start]");
  const box = (await handle.boundingBox())!;
  const lane = (await page.locator(".tl-content").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(lane.x + lane.width * 0.13, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  const first = page.locator(".tl-seg.kept").first();
  await expect.poll(() => first.getAttribute("data-w0")).not.toBe("0");
  const k = Number(await first.getAttribute("data-w0"));
  expect(k).toBeGreaterThan(1);
  for (let i = 0; i < k; i++) await expect(word(page, i)).toHaveClass(/\bcut\b/);
  await expect(word(page, k)).not.toHaveClass(/\bcut\b/);
  // the kept video now starts exactly where the handle sits
  const h = (await handle.boundingBox())!;
  const s = (await first.boundingBox())!;
  expect(Math.abs(h.x + h.width / 2 - s.x)).toBeLessThan(2);
});

test("keyboard shortcuts", async ({ page }) => {
  await open(page);
  const tc = page.locator(".pv-tc");

  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();

  const t0 = clockSeconds((await tc.textContent())!);
  await page.keyboard.press("l");
  await expect.poll(async () => clockSeconds((await tc.textContent())!)).toBeCloseTo(t0 + 5, 0);
  await page.keyboard.press("j");
  await expect.poll(async () => clockSeconds((await tc.textContent())!)).toBeCloseTo(t0, 0);

  await word(page, 9).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Cut word" })).toHaveCount(0);
  await page.keyboard.press("F2");
  await expect(page.getByRole("textbox", { name: "Correct spelling" })).toBeFocused();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Delete");
  await expect(word(page, 9)).toHaveClass(/\bcut\b/);
  await page.keyboard.press("Control+z");
  await expect(word(page, 9)).not.toHaveClass(/\bcut\b/);
  await page.keyboard.press("Control+Shift+z");
  await expect(word(page, 9)).toHaveClass(/\bcut\b/);

  // S is the split tool's key; splitting needs an engine op that does not exist yet, so it stays disabled
  await expect(page.getByRole("button", { name: "Split" })).toBeDisabled();

  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("ArrowDown");
  await expect(page).toHaveURL(/\/edit\/h02$/);
  await page.keyboard.press("ArrowUp");
  await expect(page).toHaveURL(/\/edit\/h01$/);
});

test("Previous and Next clip, and Export clips goes to export", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: "Previous clip" })).toBeDisabled();
  await page.getByRole("button", { name: "Next clip" }).click();
  await expect(page).toHaveURL(/\/edit\/h02$/);
  await expect(page.locator(".pv-title")).toContainText("The ₱500 mistake on my first gig");
  await page.getByRole("button", { name: "Previous clip" }).click();
  await expect(page).toHaveURL(/\/edit\/h01$/);
  const exp = page.getByRole("button", { name: /Export clips/ });
  await expect(exp).toContainText("8");
  await page.getByRole("checkbox", { name: /Keep Long story about my laptop/ }).check({ force: true });
  await expect(exp).toContainText("9");
  await exp.click();
  await expect(page).toHaveURL(/\/p\/mock-1\/export$/);
});

test("the shape switch reshapes the preview", async ({ page }) => {
  await open(page);
  const frame = page.locator(".pv-frame");
  const tall = (await frame.boundingBox())!;
  expect(tall.height).toBeGreaterThan(tall.width);
  await page.getByRole("radio", { name: "16:9" }).check();
  await expect.poll(async () => { const b = (await frame.boundingBox())!; return b.width > b.height; }).toBe(true);
});

for (const theme of ["dark", "light"]) {
  test(`no serious accessibility problems (${theme})`, async ({ page }) => {
    await page.goto("/#/");
    await page.evaluate((t) => localStorage.setItem("vedit.theme", t), theme);
    await open(page);
    const tabs = ["Transcript", "Captions", "Framing", "Sound"];
    for (const tab of tabs) {
      await page.getByRole("tab", { name: tab }).click();
      if (tab === "Transcript") await word(page, 3).click(); // the word popover open too
      const res = await new AxeBuilder({ page }).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    }
  });
}

test("the Edit screen has no horizontal overflow at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await open(page);
  const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(m.doc).toBe(m.inner);
  // the order on a phone: player, inspector, clips, timeline
  const y = async (sel: string) => (await page.locator(sel).boundingBox())!.y;
  expect(await y(".ed-player")).toBeLessThan(await y(".ed-insp"));
  expect(await y(".ed-insp")).toBeLessThan(await y(".ed-clips"));
  expect(await y(".ed-clips")).toBeLessThan(await y(".ed-tl"));
});
