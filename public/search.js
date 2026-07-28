const form = document.querySelector("#global-search-form");
const base = form?.dataset.base || "/journals/";
const statusElement = document.querySelector("#global-search-status");
const results = document.querySelector("#global-search-results");
const cache = new Map();

const escapeHtml = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character]);

const loadRecords = async (scope) => {
  if (!cache.has(scope)) {
    const endpoint = `${base}api/v1/search/${scope === "all" ? "all" : "latest"}.json`;
    cache.set(scope, fetch(endpoint).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }));
  }
  return cache.get(scope);
};

const searchableText = (record) => [
  record.title_en,
  record.title_cn,
  ...(record.authors || []),
  record.abstract_en,
  record.abstract_cn,
  record.journal_short_name,
  record.journal_name,
  record.volume,
  record.issue,
  record.issue_label,
  record.publication_date,
].join(" ").toLocaleLowerCase();

const populateJournals = async () => {
  const select = document.querySelector("#global-search-journal");
  if (!select) return;
  try {
    const collections = await Promise.all(
      ["top5", "fields"].map((collection) =>
        fetch(`${base}api/v1/collections/${collection}.json`).then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        })
      )
    );
    const journals = new Map();
    collections.flatMap((payload) => payload.journals || []).forEach((journal) => {
      journals.set(journal.journal_id, journal);
    });
    select.insertAdjacentHTML(
      "beforeend",
      [...journals.values()].map((journal) =>
        `<option value="${escapeHtml(journal.journal_id)}">${escapeHtml(journal.short_name)} · ${escapeHtml(journal.name)}</option>`
      ).join("")
    );
  } catch {
    // The keyword search remains usable even if the journal list cannot load.
  }
};

const render = (records) => {
  if (!records.length) {
    results.innerHTML = '<p class="empty-state">没有找到符合条件的论文。</p>';
    return;
  }
  results.innerHTML = records.slice(0, 200).map((record) => `
    <article class="search-result">
      <div class="search-result-meta">
        <span>${escapeHtml(record.journal_short_name)}</span>
        <span>${escapeHtml(record.issue_label)}</span>
        <span>${escapeHtml(record.publication_date)}</span>
        ${record.china_related ? '<span class="china-tag">中国相关</span>' : ""}
      </div>
      <h2>${escapeHtml(record.title_cn || record.title_en)}</h2>
      <p class="paper-title-en">${escapeHtml(record.title_en)}</p>
      <p class="paper-authors">${escapeHtml((record.authors || []).join(", "))}</p>
      <div class="search-result-abstracts">
        <p>${escapeHtml(record.abstract_en)}</p>
        <p>${escapeHtml(record.abstract_cn)}</p>
      </div>
      <footer class="paper-meta">
        ${record.doi ? `<span>DOI ${escapeHtml(record.doi)}</span>` : "<span></span>"}
        <a href="${escapeHtml(record.source_url)}" target="_blank" rel="noreferrer">查看期刊原文</a>
      </footer>
    </article>
  `).join("");
  if (records.length > 200) {
    results.insertAdjacentHTML(
      "beforeend",
      `<p class="result-limit">共 ${records.length} 条，当前展示前 200 条。请增加筛选条件缩小范围。</p>`,
    );
  }
};

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.querySelector("#global-search-query").value.trim().toLocaleLowerCase();
  const journal = document.querySelector("#global-search-journal").value;
  const field = document.querySelector("#global-search-field").value;
  const year = document.querySelector("#global-search-year").value.trim();
  const volume = document.querySelector("#global-search-volume").value.trim().toLocaleLowerCase();
  const issue = document.querySelector("#global-search-issue").value.trim().toLocaleLowerCase();
  const chinaOnly = document.querySelector("#global-search-china").checked;
  const scope = document.querySelector("#global-search-history").checked ? "all" : "latest";
  statusElement.textContent = scope === "all" ? "正在载入历史卷期索引……" : "正在载入最新卷期索引……";
  results.innerHTML = "";
  try {
    const payload = await loadRecords(scope);
    const filtered = (payload.records || []).filter((record) =>
      (!query || searchableText(record).includes(query)) &&
      (journal === "all" || record.journal_id === journal) &&
      (field === "all" || record.field === field) &&
      (!year || String(record.publication_date || "").includes(year)) &&
      (!volume || String(record.volume || "").toLocaleLowerCase() === volume) &&
      (!issue || String(record.issue || "").toLocaleLowerCase() === issue) &&
      (!chinaOnly || record.china_related)
    );
    statusElement.textContent = `找到 ${filtered.length} 篇论文 · ${scope === "all" ? "全部历史卷期" : "仅最新卷期"}`;
    render(filtered);
  } catch (error) {
    statusElement.textContent = "检索索引载入失败，请稍后重试。";
    results.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
  }
});

populateJournals();
