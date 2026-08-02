import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SITE = "https://academic-door.github.io/journals";
const API = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../public/api/v1"
);
const MAX_ITEMS = 60;

const readJson = (file) =>
  existsSync(file) ? JSON.parse(readFileSync(file, "utf-8")) : null;

const escapeXml = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
  })[character]);

const rssDate = (value) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? new Date().toUTCString()
    : parsed.toUTCString();
};

const summarize = (article, limit = 320) => {
  const text = (article.abstract_cn || article.abstract_en || "").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}…`;
};

const issueLabel = (issue = {}) => {
  const volume = String(issue.volume || "").trim();
  const number = String(issue.issue || "").trim();
  const existing = String(issue.issue_label || "").trim();
  const base = volume ? `Vol. ${volume}` : "";
  if (!number || number.toLowerCase() === "c") return base;
  if (existing) return existing;
  if (/^[ab]$/i.test(number)) return `${base} · Part ${number.toUpperCase()}`;
  if (/^\d+(?:\s*[-–]\s*\d+)?$/.test(number)) return `${base} · No. ${number}`;
  return [base, number].filter(Boolean).join(" · ");
};

// 订阅源在构建时生成：deploy 会先把 data 分支的 public/ 叠加上来，再执行
// astro build，因此这里读到的始终是最新一轮采集的结果，无需再让 workflow
// 单独生成一份，也不会被下一次 rm -rf public 清掉。
const collectionIssues = (collectionId) => {
  const ids = [];
  const collections =
    collectionId === "all" ? ["top5", "fields"] : [collectionId];
  for (const id of collections) {
    const payload = readJson(path.join(API, "collections", `${id}.json`));
    for (const journal of payload?.journals || []) ids.push(journal.journal_id);
  }
  const issues = [];
  for (const journalId of ids) {
    const issue = readJson(
      path.join(API, "journals", journalId, "issues", "current.json")
    );
    if (issue) issues.push(issue);
  }
  return issues.sort((a, b) =>
    String(b.retrieved_at || "").localeCompare(String(a.retrieved_at || ""))
  );
};

export function getStaticPaths() {
  return [
    { params: { collection: "all" } },
    { params: { collection: "top5" } },
    { params: { collection: "fields" } },
  ];
}

const TITLES = {
  all: "Academic Door 期刊更新",
  top5: "Academic Door · 经济学 TOP5",
  fields: "Academic Door · 领域顶刊",
};

export function GET({ params }) {
  const collection = params.collection;
  const issues = collectionIssues(collection);
  const items = [];
  for (const issue of issues) {
    const label = issueLabel(issue);
    for (const article of issue.articles || []) {
      const context = [`${issue.journal_name} ${label}`];
      const authors = (article.authors || []).join("、");
      if (authors) context.push(authors);
      const body = summarize(article);
      if (body) context.push(body);
      items.push(
        [
          "    <item>",
          `      <title>${escapeXml(article.title_cn || article.title_en || "")}</title>`,
          `      <link>${escapeXml(article.source_url || SITE)}</link>`,
          `      <guid isPermaLink="false">${escapeXml(article.paper_id || article.doi || "")}</guid>`,
          `      <pubDate>${rssDate(issue.retrieved_at)}</pubDate>`,
          `      <description>${escapeXml(context.join(" · "))}</description>`,
          "    </item>",
        ].join("\n")
      );
    }
  }

  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    "  <channel>",
    `    <title>${escapeXml(TITLES[collection] || TITLES.all)}</title>`,
    `    <link>${SITE}/</link>`,
    "    <description>经济学期刊最新卷期的中英文目录与摘要。</description>",
    "    <language>zh-cn</language>",
    `    <lastBuildDate>${rssDate(new Date().toISOString())}</lastBuildDate>`,
    `    <atom:link href="${SITE}/feeds/${collection}.xml" rel="self" type="application/rss+xml"/>`,
    items.slice(0, MAX_ITEMS).join("\n"),
    "  </channel>",
    "</rss>",
    "",
  ].join("\n");

  return new Response(body, {
    headers: { "Content-Type": "application/rss+xml; charset=utf-8" },
  });
}
