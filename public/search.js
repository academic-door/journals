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

const fetchJson = async (url) => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
};

const loadRecords = async (endpoint) => {
  if (!cache.has(endpoint)) {
    cache.set(endpoint, fetchJson(endpoint));
  }
  return cache.get(endpoint);
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

const resultCard = (record) => `
  <article class="search-result">
    <div class="search-result-meta">
      <span>${escapeHtml(record.journal_short_name)}</span>
      <span>${escapeHtml(record.issue_label)}</span>
      <span>${escapeHtml(record.publication_date)}</span>
      ${record.china_related ? '<span class="china-tag">中国相关</span>' : ""}
      ${record.publication_state === "source_pending" ? '<span class="source-pending-tag">内容已齐，待来源核验</span>' : ""}
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
`;

const PAGE_SIZE = 20;
const MAX_VISIBLE = 40;
let filteredRecords = [];
let renderedCount = 0;
let loadMoreButton = null;
let continueYearsButton = null;
let yearQueue = [];
let limitHintShown = false;

const renderVisible = () => {
  const visible = filteredRecords.slice(0, renderedCount);
  results.innerHTML = visible.map(resultCard).join("");
  if (renderedCount < filteredRecords.length && renderedCount < MAX_VISIBLE) {
    if (!loadMoreButton) {
      loadMoreButton = document.createElement("button");
      loadMoreButton.type = "button";
      loadMoreButton.className = "button load-more-button";
      loadMoreButton.textContent = "显示更多结果";
      loadMoreButton.addEventListener("click", () => {
        renderedCount = Math.min(filteredRecords.length, renderedCount + PAGE_SIZE);
        renderVisible();
      });
    }
    loadMoreButton.hidden = false;
    results.insertAdjacentElement("beforeend", loadMoreButton);
  } else if (loadMoreButton) {
    loadMoreButton.hidden = true;
  }
  const hint = document.querySelector("#result-limit-hint");
  if (renderedCount >= MAX_VISIBLE && filteredRecords.length > MAX_VISIBLE) {
    if (!limitHintShown) {
      limitHintShown = true;
      statusElement.insertAdjacentHTML(
        "beforeend",
        `<span id="result-limit-hint" class="result-limit">结果较多，已展示前 ${MAX_VISIBLE} 条。请增加筛选条件缩小范围。</span>`,
      );
    }
  } else if (hint) {
    hint.remove();
    limitHintShown = false;
  }
};

const renderSkeleton = () => {
  results.innerHTML = `
    <article class="search-result skeleton" aria-hidden="true">
      <div class="skeleton-line skeleton-meta"></div>
      <div class="skeleton-line skeleton-title"></div>
      <div class="skeleton-line skeleton-text"></div>
      <div class="skeleton-line skeleton-text"></div>
    </article>
  `.repeat(3);
};

const resetPaging = () => {
  filteredRecords = [];
  renderedCount = 0;
  loadMoreButton = null;
  continueYearsButton = null;
  limitHintShown = false;
  const hint = document.querySelector("#result-limit-hint");
  if (hint) hint.remove();
  results.innerHTML = "";
};

const matchesFilters = (record, filters) => {
  const query = filters.query;
  return (
    (!query || searchableText(record).includes(query)) &&
    (filters.journal === "all" || record.journal_id === filters.journal) &&
    (filters.field === "all" || record.field === filters.field) &&
    (!filters.year || String(record.publication_date || "").includes(filters.year)) &&
    (!filters.volume || String(record.volume || "").toLocaleLowerCase() === filters.volume) &&
    (!filters.issue || String(record.issue || "").toLocaleLowerCase() === filters.issue) &&
    (!filters.chinaOnly || record.china_related)
  );
};

const collectFilters = () => ({
  query: document.querySelector("#global-search-query").value.trim().toLocaleLowerCase(),
  journal: document.querySelector("#global-search-journal").value,
  field: document.querySelector("#global-search-field").value,
  year: document.querySelector("#global-search-year").value.trim(),
  volume: document.querySelector("#global-search-volume").value.trim().toLocaleLowerCase(),
  issue: document.querySelector("#global-search-issue").value.trim().toLocaleLowerCase(),
  chinaOnly: document.querySelector("#global-search-china").checked,
  history: document.querySelector("#global-search-history").checked,
});

const presentResults = (countLabel) => {
  renderedCount = Math.min(PAGE_SIZE, filteredRecords.length);
  statusElement.textContent = `找到 ${filteredRecords.length} 篇论文 · ${countLabel}`;
  renderVisible();
};

const loadNextYear = async (filters) => {
  if (!yearQueue.length) {
    statusElement.textContent = `找到 ${filteredRecords.length} 篇论文 · 全部历史卷期`;
    renderVisible();
    return;
  }
  const year = yearQueue[0];
  const remaining = yearQueue.length - 1;
  statusElement.textContent = `正在载入 ${year} 年卷期索引……${remaining ? `（还有 ${remaining} 个年份）` : ""}`;
  try {
    const payload = await loadRecords(`${base}api/v1/search/years/${year}.json`);
    const matched = (payload.records || []).filter((record) =>
      matchesFilters(record, filters)
    );
    filteredRecords.push(...matched);
    filteredRecords.sort(
      (left, right) =>
        String(right.publication_date || "").localeCompare(String(left.publication_date || "")) ||
        left.journal_id.localeCompare(right.journal_id)
    );
    yearQueue.shift();
    renderedCount = Math.min(PAGE_SIZE, filteredRecords.length);
    if (yearQueue.length) {
      statusElement.textContent = `已载入 ${year} 年 · 当前 ${filteredRecords.length} 篇，继续载入更早年份……`;
      if (!continueYearsButton) {
        continueYearsButton = document.createElement("button");
        continueYearsButton.type = "button";
        continueYearsButton.id = "load-earlier-years";
        continueYearsButton.className = "button";
        continueYearsButton.textContent = "继续载入更早年份";
        continueYearsButton.addEventListener("click", () => loadNextYear(filters));
      }
      renderVisible();
      results.insertAdjacentElement("beforeend", continueYearsButton);
    } else {
      statusElement.textContent = `找到 ${filteredRecords.length} 篇论文 · 全部历史卷期`;
      renderVisible();
    }
  } catch (error) {
    results.insertAdjacentHTML(
      "beforeend",
      `<p class="error-message">${year} 年索引载入失败（${escapeHtml(error.message)}）。
       <button id="retry-year" class="button" type="button">重试</button></p>`,
    );
    document.querySelector("#retry-year")?.addEventListener("click", () => {
      document.querySelector("#retry-year")?.closest("p")?.remove();
      loadNextYear(filters);
    });
  }
};

const runSearch = async () => {
  const filters = collectFilters();
  resetPaging();
  let index = {};
  try {
    index = await loadRecords(`${base}api/v1/search/index.json`);
  } catch {
    // Metadata is a convenience; every code path below falls back to the
    // legacy single-file indexes when the manifest is unavailable.
  }
  const years = (index.years || []).map((entry) => entry.year).sort((a, b) => b - a);

  // A China-only latest search reads the dedicated small index directly.
  if (filters.chinaOnly && !filters.history && !filters.year) {
    statusElement.textContent = "正在载入中国相关论文……";
    renderSkeleton();
    try {
      const payload = await loadRecords(
        index.china_latest_url || `${base}api/v1/search/china-latest.json`
      );
      filteredRecords = (payload.records || []).filter((record) =>
        matchesFilters(record, filters)
      );
      presentResults("中国相关 · 仅最新卷期");
    } catch (error) {
      // Transition fallback: older data branches may not publish the
      // dedicated China index yet; filter the regular latest index instead.
      try {
        const payload = await loadRecords(`${base}api/v1/search/latest.json`);
        filteredRecords = (payload.records || []).filter((record) =>
          matchesFilters(record, filters)
        );
        presentResults("中国相关 · 仅最新卷期（回退索引）");
      } catch (fallbackError) {
        statusElement.textContent = "中国相关索引载入失败，请稍后重试。";
        results.innerHTML = `<p class="error-message">${escapeHtml(fallbackError.message)}</p>`;
      }
    }
    return;
  }

  // A specific year only downloads that year's slice.
  if (filters.year) {
    statusElement.textContent = `正在载入 ${filters.year} 年卷期索引……`;
    renderSkeleton();
    try {
      const payload = await loadRecords(
        `${base}api/v1/search/years/${filters.year}.json`
      );
      filteredRecords = (payload.records || []).filter((record) =>
        matchesFilters(record, filters)
      );
      presentResults(`${filters.year} 年${filters.history ? " · 历史卷期" : ""}`);
    } catch (error) {
      statusElement.textContent = `${filters.year} 年索引载入失败，请稍后重试。`;
      results.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
    }
    return;
  }

  // Latest scope keeps the existing single-file behavior.
  if (!filters.history) {
    statusElement.textContent = "正在载入最新卷期索引……";
    renderSkeleton();
    try {
      const payload = await loadRecords(`${base}api/v1/search/latest.json`);
      filteredRecords = (payload.records || []).filter((record) =>
        matchesFilters(record, filters)
      );
      presentResults("仅最新卷期");
    } catch (error) {
      statusElement.textContent = "检索索引载入失败，请稍后重试。";
      results.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
    }
    return;
  }

  // History without a year: load newest-first year by year so results appear
  // before the entire archive has downloaded. Older data branches without
  // year slices fall back to the single all.json file.
  yearQueue = [...years];
  if (!yearQueue.length) {
    statusElement.textContent = "正在载入全部历史卷期索引……";
    renderSkeleton();
    try {
      const payload = await loadRecords(`${base}api/v1/search/all.json`);
      filteredRecords = (payload.records || []).filter((record) =>
        matchesFilters(record, filters)
      );
      presentResults("全部历史卷期（回退索引）");
    } catch (error) {
      statusElement.textContent = "检索索引载入失败，请稍后重试。";
      results.innerHTML = `<p class="error-message">${escapeHtml(error.message)}</p>`;
    }
    return;
  }
  await loadNextYear(filters);
};

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch();
});

const initializeFromQuery = async () => {
  const [journalReady, indexReady] = await Promise.allSettled([
    populateJournals(),
    loadRecords(`${base}api/v1/search/index.json`),
  ]);
  await Promise.all([journalReady, indexReady]);
  const params = new URLSearchParams(window.location.search);
  const values = {
    query: params.get("q") || "",
    journal: params.get("journal") || "all",
    field: params.get("field") || "all",
    year: params.get("year") || "",
    volume: params.get("volume") || "",
    issue: params.get("issue") || "",
  };
  document.querySelector("#global-search-query").value = values.query;
  document.querySelector("#global-search-year").value = values.year;
  document.querySelector("#global-search-volume").value = values.volume;
  document.querySelector("#global-search-issue").value = values.issue;
  const journalSelect = document.querySelector("#global-search-journal");
  const fieldSelect = document.querySelector("#global-search-field");
  if ([...journalSelect.options].some((option) => option.value === values.journal)) {
    journalSelect.value = values.journal;
  }
  if ([...fieldSelect.options].some((option) => option.value === values.field)) {
    fieldSelect.value = values.field;
  }
  document.querySelector("#global-search-china").checked = params.get("china") === "1";
  document.querySelector("#global-search-history").checked = params.get("history") === "1";
  const hasPreset = [...params.keys()].some((key) =>
    ["q", "journal", "field", "year", "volume", "issue", "china", "history"].includes(key)
  );
  if (hasPreset) form.requestSubmit();
};

initializeFromQuery();
