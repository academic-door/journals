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
  for (const path of ["", "top5/", "fields/", "search/", "status/", "composer/", "themes/"]) {
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
    const readerCopy = await page.locator("body").innerText();
    for (const jargon of ["公开页面只展示结果", "公开端不会加载私有", "内部运维仪表盘", "source-authority", "canonical archive", "expected set", "私有 renderer"]) {
      assert.ok(!readerCopy.includes(jargon), `${url} leaked internal narration: ${jargon}`);
    }
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
    assert.ok(await page.getByRole("heading", { name: "Academic Door · 期刊" }).isVisible());
    assert.ok(await page.getByRole("link", { name: "进入顶刊之门" }).isVisible());
    assert.ok(await page.getByRole("link", { name: "浏览领域之门" }).isVisible());
    assert.ok(await page.getByText(/监测期刊：顶刊 5 本 \+ 领域 44 本/).isVisible());
    assert.ok(await page.getByText(/预览数据快照.*非实时统计/).isVisible());
    assert.ok(await page.getByText("已确认缺失卷期").isVisible());
    assert.equal(await page.getByText("监测失败").count(), 0);
    await page.close();
  }

  {
    const { page } = await open("search/");
    assert.ok(await page.getByText(/全部 49 本监测期刊（顶刊 5 \+ 领域 44）/).isVisible());
    assert.ok(await page.getByText("浏览 44 本领域期刊（不含 Top Five）").isVisible());
    await page.close();
  }

  {
    const { page } = await open("composer/?journal=aer&issue=aer-116-9");
    assert.ok(await page.getByRole("heading", { name: "发布预览" }).isVisible());
    assert.ok(await page.getByRole("heading", { name: "文章排版示例" }).isVisible());
    assert.equal(await page.locator(".capability-list, .showcase-notes").count(), 0);
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
    assert.ok(await page.getByRole("heading", { name: "数据说明" }).isVisible());
    assert.equal(await page.locator(".history-table").count(), 0);
    assert.equal(await page.locator(".quality-table").count(), 0);
    assert.ok(await page.getByRole("heading", { name: "来源与更新" }).isVisible());
    assert.ok(await page.getByText("已收录卷期", { exact: true }).isVisible());
    assert.ok(await page.getByText("已确认尚未收录的卷期", { exact: true }).isVisible());
    assert.equal(await page.getByText("监测失败").count(), 0);
    await page.close();
  }

  {
    const { page } = await open("themes/");
    assert.ok(await page.getByRole("heading", { name: "此页面已停止使用" }).isVisible());
    assert.ok(await page.getByRole("link", { name: "查看发布预览" }).isVisible());
    await page.close();
  }

  console.log(`Journals public UX preview smoke passed at ${width}px`);
} finally {
  await browser.close();
}
