import { expect, test } from "@playwright/test";

// The mock engine's folder holds first-gig.jpg, "new_year party.mp4", office.png and exit-door.jpg. Clip h01 of
// mock-1 says "The first year ..." and "my first gig", so the year video and the gig photo are suggested.

async function openBroll(page: import("@playwright/test").Page) {
  await page.goto("/#/p/mock-1/edit/h01");
  await expect(page.locator('.tx [data-w="0"]')).toBeVisible();
  await page.getByRole("tab", { name: "B-roll", exact: true }).click();
  return page.getByRole("tabpanel", { name: "B-roll" });
}

test("suggest b-roll from a folder, add one, skip one, and undo", async ({ page }) => {
  const tool = await openBroll(page);
  const suggest = tool.getByRole("button", { name: "Suggest b-roll" });
  await expect(suggest).toBeDisabled();
  await tool.getByRole("textbox", { name: "Folder of photos and videos" }).fill("C:/Users/me/Pictures/B-roll");
  await suggest.click();

  const list = tool.getByRole("list", { name: "B-roll suggestions" });
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await expect(list.getByRole("listitem").first()).toContainText("new_year party.mp4");
  await expect(list.getByRole("listitem").first()).toContainText("“year”");
  await expect(list.getByRole("listitem").nth(1)).toContainText("first-gig.jpg");

  await tool.getByRole("button", { name: "Add first-gig.jpg" }).click();
  const blocks = page.locator(".tl-ov-broll .tl-ovb");
  await expect(blocks).toHaveCount(1);
  await expect(blocks.first()).toHaveText("first-gig.jpg");
  await expect(tool.locator(".ly-item")).toHaveCount(1);

  await tool.getByRole("button", { name: "Skip new_year party.mp4" }).click();
  await expect(tool.getByText("That's every suggestion.")).toBeVisible();
  await expect(blocks).toHaveCount(1);

  // asking again leaves out the photo already on the clip
  await suggest.click();
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list).toContainText("new_year party.mp4");

  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(blocks).toHaveCount(0);
});

test("Add all places every suggestion in one undo step, and the folder is remembered", async ({ page }) => {
  const tool = await openBroll(page);
  await tool.getByRole("textbox", { name: "Folder of photos and videos" }).fill("C:/Pics");
  await tool.getByRole("button", { name: "Suggest b-roll" }).click();
  await tool.getByRole("button", { name: "Add all" }).click();
  const blocks = page.locator(".tl-ov-broll .tl-ovb");
  await expect(blocks).toHaveCount(2);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(blocks).toHaveCount(0);

  await page.reload();
  const again = await openBroll(page);
  await expect(again.getByRole("textbox", { name: "Folder of photos and videos" })).toHaveValue("C:/Pics");
});

test("a relative folder and an empty one are explained", async ({ page }) => {
  const tool = await openBroll(page);
  const folder = tool.getByRole("textbox", { name: "Folder of photos and videos" });
  await folder.fill("Pictures");
  await folder.press("Enter");
  await expect(tool.getByRole("alert")).toContainText("folder must be an existing absolute folder");
  await folder.fill("D:/empty");
  await folder.press("Enter");
  await expect(tool.getByText("That folder has no photos or videos.")).toBeVisible();
});
