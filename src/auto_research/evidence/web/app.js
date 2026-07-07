const state = { paper: null, papers: [], rows: [], selected: null, filter: "", search: "" };
const fields = ["value_text", "meaning", "unit", "article_title", "doi", "context_explanation"];
const fieldLabels = {
  value_text: "具体数值",
  meaning: "具体意义",
  unit: "单位",
  article_title: "文章题目",
  doi: "DOI",
  context_explanation: "数据在文中的解释",
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `请求失败 ${response.status}`);
  return body;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function toast(message, error = false) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.className = error ? "show error" : "show";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.className = "";
  }, 3200);
}

function setText(id, value) {
  document.getElementById(id).textContent = value ?? "";
}

function paperRef(paper) {
  return paper?.local_article_key || paper?.zotero_key || paper?.pilot_code || String(paper?.id || paper?.paper_id || "");
}

function paperLabel(paper) {
  const ref = paperRef(paper);
  const title = paper?.title || "未命名文章";
  return ref ? `${ref} · ${title}` : title;
}

function renderOriginalPlaceholder() {
  document.querySelector("#original-pane").innerHTML = '<div class="blank"><span>↖</span><h3>点击左侧任意一行</h3><p>这里会显示不可修改的自动提取原始版本，以及它在论文中的页码与出处。</p></div>';
}

async function load() {
  state.papers = await api("/api/papers");
  await loadCurrentPaper();
  renderPaperOptions();
}

async function loadCurrentPaper() {
  [state.paper, state.rows] = await Promise.all([api("/api/current-paper"), api("/api/six-data")]);
  state.selected = null;
  renderPaperOptions();
  renderPaper();
  renderTable();
  renderOriginalPlaceholder();
  renderHistory();
  fillManualDefaults();
}

function renderPaperOptions() {
  const datalist = document.querySelector("#paper-options");
  if (!datalist) return;
  datalist.innerHTML = state.papers.map(paper => `<option value="${esc(paperRef(paper))}" label="${esc(paper.title || "")}"></option>`).join("");
  const input = document.querySelector("#paper-switch-input");
  if (input && state.paper) input.value = paperRef(state.paper);
}

function renderPaper() {
  const paper = state.paper;
  if (!paper) return;
  setText("paper-key", paperRef(paper));
  setText("paper-title", paper.title);
  setText("paper-doi", paper.doi || "—");
  setText("mini-title", paper.title);
  setText("mini-doi", paper.doi || "—");
  setText("nav-count", state.rows.length);
  document.querySelector("#open-paper").href = `/api/papers/${paper.id}/pdf`;
}

function filteredRows() {
  const q = state.filter.trim().toLowerCase();
  if (!q) return state.rows;
  return state.rows.filter(row => fields.some(field => String(row[field] || "").toLowerCase().includes(q)));
}

function renderTable() {
  const rows = filteredRows();
  setText("row-count", `${rows.length} 条`);
  const body = document.querySelector("#edit-rows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty-table">这篇文章目前还没有六列抽取结果。你可以先人工补录，或继续扩展自动抽取。</div></td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => {
    const cls = [state.selected === row.item_id ? "selected" : "", row.version_no > 0 ? "revised" : "", row.origin_type === "manual" ? "manual" : ""].filter(Boolean).join(" ");
    const cells = fields.map(field => `<td><textarea class="cell ${field === "context_explanation" ? "context" : ""}" data-field="${field}" aria-label="${fieldLabels[field]}">${esc(row[field])}</textarea></td>`).join("");
    const badge = row.origin_type === "manual" ? "人工" : row.version_no > 0 ? `已修正 v${row.version_no}` : "未修正";
    return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action"><button class="confirm" data-confirm="${row.item_id}">确认修正</button><button data-original="${row.item_id}">查看原始</button><small>${badge}</small></div></td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-item]").forEach(tr => tr.addEventListener("click", event => {
    if (event.target.closest("button[data-confirm]")) return;
    selectRow(Number(tr.dataset.item));
  }));
  body.querySelectorAll("[data-original]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    selectRow(Number(btn.dataset.original));
  }));
  body.querySelectorAll("[data-confirm]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    confirmRow(Number(btn.dataset.confirm));
  }));
}

function selectRow(id) {
  state.selected = id;
  const row = state.rows.find(item => item.item_id === id);
  renderOriginal(row);
  document.querySelectorAll("#edit-rows tr[data-item]").forEach(tr => tr.classList.toggle("selected", Number(tr.dataset.item) === id));
}

function originalValue(row, field) {
  return row.origin_type === "automatic" ? row[`original_${field}`] : null;
}

function renderOriginal(row) {
  if (!row) {
    renderOriginalPlaceholder();
    return;
  }
  const pane = document.querySelector("#original-pane");
  if (row.origin_type === "manual") {
    pane.innerHTML = '<div class="blank"><span>＋</span><h3>人工补录数据</h3><p>这条记录不是自动提取结果，因此没有不可变的原始版本。</p></div>';
    return;
  }
  const sourcePage = row.original_source_page;
  pane.innerHTML = `<header class="original-head"><span>IMMUTABLE ORIGINAL · #${row.item_id}</span><h3>${esc(row.original_meaning)}</h3></header><div class="original-grid">${fields.map(field => `<div class="original-field ${field === "context_explanation" ? "context" : ""}"><small>${fieldLabels[field]}</small><p>${esc(originalValue(row, field))}</p></div>`).join("")}</div><div class="provenance"><strong>论文定位</strong><p>PDF第 ${esc(sourcePage || "?")} 页 · ${esc(row.original_source_locator || "未标注")}<br>${esc(row.original_source_excerpt || "")}</p><button class="source-open" type="button" data-source-open="${row.item_id}">打开原文定位并高亮 →</button></div>`;
  pane.querySelector("[data-source-open]")?.addEventListener("click", () => openSourceViewer(row.item_id));
}

function sourceMetaLoading(row) {
  return `<article class="source-meta-card"><strong>正在定位</strong><p>${esc(row.original_meaning || row.meaning)}<br>PDF第 ${esc(row.original_source_page || row.source_page || "?")} 页 · ${esc(row.original_source_locator || row.source_locator || "未标注")}</p></article>`;
}

function sourceMetaHtml(data) {
  return `<article class="source-meta-card"><strong>${esc(data.match_label)}</strong><p>PDF第 ${esc(data.page_number)} 页 · ${esc(data.locator || "未标注")}<br>${esc(data.match_note)}</p></article><article class="source-meta-card"><strong>证据片段</strong><p>${esc(data.excerpt || "未保留原始证据片段")}</p></article>`;
}

async function openSourceViewer(id) {
  const row = state.rows.find(item => item.item_id === id);
  if (!row) return;
  const dialog = document.querySelector("#source-dialog");
  const meta = document.querySelector("#source-meta");
  const image = document.querySelector("#source-image");
  const pdfLink = document.querySelector("#source-open-pdf");
  meta.innerHTML = sourceMetaLoading(row);
  image.removeAttribute("src");
  image.alt = "正在加载高亮原文页";
  pdfLink.removeAttribute("href");
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute("open", "open");
  }
  try {
    const data = await api(`/api/six-data/${id}/source-view`);
    meta.innerHTML = sourceMetaHtml(data);
    image.src = `${data.image_url}?ts=${Date.now()}`;
    image.alt = `${data.match_label}：PDF 第 ${data.page_number} 页`;
    pdfLink.href = data.pdf_url;
  } catch (error) {
    meta.innerHTML = `<article class="source-meta-card error"><strong>定位失败</strong><p>${esc(error.message)}</p></article>`;
    toast(error.message, true);
  }
}

function collectRowFields(id) {
  const tr = document.querySelector(`tr[data-item="${id}"]`);
  if (!tr) throw new Error("当前表格中找不到这条数据");
  const values = {};
  tr.querySelectorAll("[data-field]").forEach(input => {
    values[input.dataset.field] = input.value;
  });
  return values;
}

async function confirmRow(id) {
  try {
    const values = collectRowFields(id);
    const current = state.rows.find(row => row.item_id === id);
    const changed = fields.filter(field => String(values[field] ?? "") !== String(current[field] ?? ""));
    if (!changed.length) {
      toast("没有检测到修改；原始数据保持不变。");
      return;
    }
    const result = await api(`/api/six-data/${id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fields: values,
        editor: "本地研究者",
        note: `修改字段：${changed.map(field => fieldLabels[field]).join("、")}`,
      }),
    });
    state.rows = state.rows.map(row => row.item_id === id ? result : row);
    state.selected = id;
    renderTable();
    renderOriginal(result);
    renderHistory();
    toast(`已确认修正并创建版本 v${result.version_no}；自动提取原始版本未改变。`);
  } catch (error) {
    toast(error.message, true);
  }
}

function fillManualDefaults() {
  const form = document.querySelector("#manual-form");
  if (!form || !state.paper) return;
  form.elements.article_title.value = state.paper.title || "";
  form.elements.doi.value = state.paper.doi || "";
}

async function saveManual(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const values = {};
  fields.forEach(field => {
    values[field] = form.elements[field].value;
  });
  try {
    const result = await api("/api/six-data/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, fields: values, editor: "本地研究者" }),
    });
    state.rows.push(result);
    form.reset();
    fillManualDefaults();
    renderTable();
    renderHistory();
    setText("nav-count", state.rows.length);
    toast("人工数据已保存；该记录没有自动提取原始版本。");
  } catch (error) {
    toast(error.message, true);
  }
}

async function runSearch(event) {
  event?.preventDefault();
  const q = document.querySelector("#search-query").value.trim();
  state.search = q;
  try {
    const rows = await api(`/api/six-search?q=${encodeURIComponent(q)}`);
    setText("search-summary", q ? `“${q}” 找到 ${rows.length} 条相关数据` : `显示全部 ${rows.length} 条数据`);
    document.querySelector("#search-export").href = `/api/six-export.csv?q=${encodeURIComponent(q)}`;
    renderResults(rows);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderResults(rows) {
  const el = document.querySelector("#search-results");
  if (!rows.length) {
    el.innerHTML = '<div class="blank"><h3>没有找到相关数据</h3><p>尝试材料名称、环境条件、物理量或它们的组合。</p></div>';
    return;
  }
  el.innerHTML = rows.map(row => `<article class="result"><div class="value">${esc(row.value_text)}<small> ${esc(row.unit)}</small></div><strong>${esc(row.meaning)}</strong><em>${row.search_score != null ? `相关度 ${esc(row.search_score)}` : ""}</em><p>${esc(row.context_explanation)}</p><div class="result-paper">${esc(paperRef(row))} · ${esc(row.article_title)}</div><button class="row-link" data-jump="${row.item_id}">去校对</button></article>`).join("");
  el.querySelectorAll("[data-jump]").forEach(btn => btn.addEventListener("click", () => jumpToRow(Number(btn.dataset.jump))));
}

async function jumpToRow(id) {
  try {
    let row = state.rows.find(item => item.item_id === id);
    if (!row) {
      const item = await api(`/api/six-data/${id}`);
      if (item.paper_id !== state.paper.id) {
        await switchCurrentPaper({ paperId: item.paper_id, silent: true });
      }
      row = state.rows.find(itemRow => itemRow.item_id === id);
    }
    state.filter = "";
    document.querySelector("#table-filter").value = "";
    switchView("review");
    renderTable();
    selectRow(id);
    document.querySelector(`tr[data-item="${id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  } catch (error) {
    toast(error.message, true);
  }
}

function renderHistory() {
  const changed = state.rows.filter(row => row.version_no > 0 || row.origin_type === "manual").sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const el = document.querySelector("#history-list");
  if (!changed.length) {
    el.innerHTML = '<div class="blank"><h3>还没有已确认修正</h3><p>左侧表格里的临时输入不会出现在这里。</p></div>';
    return;
  }
  el.innerHTML = changed.map(row => `<article class="history-card"><span>${row.origin_type === "manual" ? "人工补录" : `版本 v${row.version_no}`}</span><div><strong>${esc(row.meaning)} · ${esc(row.value_text)} ${esc(row.unit)}</strong><p>${esc(row.context_explanation)}</p></div><div><strong>${esc(row.editor)}</strong><p>${esc(row.edit_note || "")}<br>${esc(row.created_at)}</p></div></article>`).join("");
}

function switchView(name) {
  document.querySelectorAll(".nav,.view").forEach(el => el.classList.remove("active"));
  document.querySelector(`.nav[data-view="${name}"]`).classList.add("active");
  document.querySelector(`#view-${name}`).classList.add("active");
  if (name === "search" && !document.querySelector("#search-results").children.length) runSearch();
}

async function switchCurrentPaper({ articleKey = null, paperId = null, silent = false } = {}) {
  const payload = {};
  if (paperId != null) payload.paper_id = paperId;
  if (articleKey != null) payload.article_key = articleKey;
  await api("/api/current-paper", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await loadCurrentPaper();
  if (!silent) toast(`已切换到 ${paperRef(state.paper)}。`);
}

async function submitPaperSwitch(event) {
  event.preventDefault();
  const input = document.querySelector("#paper-switch-input");
  const key = input.value.trim();
  if (!key) {
    toast("请先输入文章号。", true);
    return;
  }
  try {
    await switchCurrentPaper({ articleKey: key });
  } catch (error) {
    toast(error.message, true);
  }
}

document.querySelectorAll(".nav").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
document.querySelector("#table-filter").addEventListener("input", event => {
  state.filter = event.target.value;
  renderTable();
});
document.querySelector("#search-form").addEventListener("submit", runSearch);
document.querySelector("#manual-form").addEventListener("submit", saveManual);
document.querySelector("#paper-switch-form").addEventListener("submit", submitPaperSwitch);
document.querySelector("[data-close-source]")?.addEventListener("click", () => document.querySelector("#source-dialog")?.close());
document.querySelector("#source-dialog")?.addEventListener("click", event => {
  const dialog = event.currentTarget;
  if (event.target === dialog) dialog.close();
});
load().catch(error => toast(error.message, true));
