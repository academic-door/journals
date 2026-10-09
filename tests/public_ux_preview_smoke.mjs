import assert from "node:assert/strict";
import { chromium } from "playwright";

const root = process.env.PREVIEW_URL || "http://127.0.0.1:8777/";
const width = Number(process.env.VIEWPORT_WIDTH || 1440);
const mobile = width <= 480;
const browser = await chromium.launch({ headless: true });

async function open(path) {
  const page = await browser.newPage({ viewport: { width, height: mobile ? 844 : 960 } });
  const url = new URL(path, root).href;
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  assert.equal(response?.status(), 200, `${url} did not return 200`);
  await page.waitForTimeout(300);
  return { page, url };
}

try {
  for (const path of ["", "top5/", "fields/", "search/", "status/", "composer/"]) {
    const { page, url } = await open(path);
    assert.ok(await page.locator(".preview-banner").isVisible(), `${url} preview banner missing`);
    const robots = await page.locator('meta[name="robots"]').getAttribute("content");
    assert.match(robots || "", /noindex/);
    assert.match(robots || "", /nofollow/);
    assert.match(robots || "", /noarchive/);
    assert.equal(
      await page.locator(".parent-brand").getAttribute("href"),
      "https://academic-door.github.io/",
      `${url} parent route`,
    );
    assert.ok(await page.locator(".child-brand").isVisible(), `${url} child identity missing`);
    const nav = (await page.locator(".reader-nav a").allTextContents()).map((value) => value.trim());
    assert.deepEqual(nav, ["顶刊之门", "领域之门", "跨刊检索"], `${url} nav order`);
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      `${url} horizontal overflow`,
    );
    await page.close();
  }

  {
    const { page } = await open("");
    assert.ok(await page.getByRole("heading", { name: "从顶刊最新一期，到领域期刊历史卷期。" }).isVisible());
    assert.ok(await page.getByRole("link", { name: "进入顶刊之门" }).isVisible());
    assert.ok(await page.getByRole("link", { name: "浏览领域之门" }).isVisible());
    await page.close();
  }

  {
    const { page } = await open("composer/?journal=aer&issue=aer-116-9");
    assert.ok(await page.getByRole("heading", { name: "发布预览" }).isVisible());
    assert.equal(await page.locator("#markdown-editor").count(), 0);
    assert.equal(await page.locator("#copy-rich").count(), 0);
    const privateHref = await page.locator("#private-composer-link").getAttribute("href");
    assert.match(privateHref || "", /academic-door-composer\.academic-door\.workers\.dev/);
    assert.match(privateHref || "", /journal=aer/);
    assert.match(privateHref || "", /issue=aer-116-9/);
    await page.close();
  }

  {
    const { page } = await open("status/");
    assert.ok(await page.getByRole("heading", { name: "数据状态" }).isVisible());
    assert.equal(await page.locator(".history-table").count(), 0);
    assert.equal(await page.locator(".quality-table").count(), 0);
    await page.close();
  }

  console.log(`Journals public UX preview smoke passed at ${width}px`);
} finally {
  await browser.close();
}
