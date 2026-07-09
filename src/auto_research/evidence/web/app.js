const state = { paper: null, papers: [], rows: [], selected: null, filter: "", reviewFilter: "all", search: "", extraction: null, learning: null, allLearning: null, audit: null, deepseekRun: null, uploads: [], jobs: [], ai: null, dirtyRows: new Set(), progressTimer: null, progressValue: 0 };
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

function hasUnsavedEdits() {
  return state.dirtyRows.size > 0;
}

function clearDirtyRows(itemId = null) {
  if (itemId == null) state.dirtyRows.clear();
  else state.dirtyRows.delete(Number(itemId));
}

function confirmDiscardUnsaved(actionLabel) {
  if (!hasUnsavedEdits()) return true;
  return window.confirm(
    `当前有 ${state.dirtyRows.size} 行修改尚未确认。\n\n` +
    `继续“${actionLabel}”将放弃这些未写入数据库的临时修改。\n\n` +
    "是否继续？"
  );
}

function markRowDirty(itemId, dirty) {
  const id = Number(itemId);
  if (dirty) state.dirtyRows.add(id);
  else state.dirtyRows.delete(id);
  document.querySelector(`tr[data-item="${id}"]`)?.classList.toggle("dirty", dirty);
}

function paperRef(paper) {
  return paper?.doi || paper?.article_title || paper?.title || `paper-${paper?.id || paper?.paper_id || ""}`;
}

function paperLabel(paper) {
  const title = paper?.title || "未命名文章";
  const details = [
    paper?.year ? `${paper.year}` : "",
    paper?.doi ? paper.doi : "",
  ].filter(Boolean);
  return details.length ? `${title} · ${details.join(" · ")}` : title;
}

function renderOriginalPlaceholder() {
  document.querySelector("#original-pane").innerHTML = '<div class="blank"><span>↖</span><h3>选择一条数据</h3><p>右侧显示原始抽取值、证据页码和原文定位。</p></div>';
}

async function load() {
  [state.papers, state.uploads, state.jobs, state.ai] = await Promise.all([
    api("/api/papers"),
    api("/api/uploads"),
    api("/api/processing-jobs"),
    api("/api/ai/status"),
  ]);
  await loadCurrentPaper();
  renderPaperOptions();
  renderUploadWorkspace();
}

async function loadCurrentPaper() {
  [state.paper, state.rows, state.extraction, state.learning, state.allLearning, state.audit, state.deepseekRun] = await Promise.all([
    api("/api/current-paper"),
    api("/api/six-data"),
    api("/api/current-paper/extraction"),
    api("/api/current-paper/learning-samples"),
    api("/api/learning-samples"),
    api("/api/current-paper/evidence-audit"),
    api("/api/current-paper/deepseek-run"),
  ]);
  clearDirtyRows();
  state.selected = null;
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderEvidenceAudit();
  renderDeepSeekRun();
  renderTable();
  renderOriginalPlaceholder();
  renderHistory();
  fillManualDefaults();
}

function renderPaperOptions() {
  const optionHtml = state.papers.map(paper => {
    const rows = Number(paper.six_row_count || paper.row_count || 0);
    const scan = rows ? ` · ${rows}条数据` : "";
    return `<option value="${esc(paper.id)}">${esc(paperLabel(paper))}${esc(scan)}</option>`;
  }).join("");
  const switchSelect = document.querySelector("#paper-switch-input");
  if (switchSelect) {
    switchSelect.innerHTML = optionHtml;
    if (state.paper) switchSelect.value = String(state.paper.id);
  }
  const manualSelect = document.querySelector("#manual-paper-select");
  if (manualSelect) {
    const previous = manualSelect.value;
    manualSelect.innerHTML = optionHtml;
    if (previous && state.papers.some(paper => String(paper.id) === previous)) {
      manualSelect.value = previous;
    } else if (state.paper) {
      manualSelect.value = String(state.paper.id);
    }
  }
}

function renderPaper() {
  const paper = state.paper;
  if (!paper) return;
  const authorYear = [paper.first_author || "作者待补", paper.year || "年份待补"].filter(Boolean).join(" · ");
  setText("paper-key", authorYear);
  setText("paper-title", paper.title);
  setText("paper-doi", paper.doi || "—");
  setText("mini-title", paper.title);
  setText("mini-doi", paper.doi || "—");
  setText("nav-count", state.rows.length);
  document.querySelector("#open-paper").href = `/api/papers/${paper.id}/pdf`;
  document.querySelector("#current-export-csv").href = `/api/current-paper/export.csv?paper_id=${encodeURIComponent(paper.id)}`;
  document.querySelector("#current-export-xlsx").href = `/api/current-paper/export.xlsx?paper_id=${encodeURIComponent(paper.id)}`;
}

function renderExtractionStatus() {
  const status = state.extraction;
  const el = document.querySelector("#paper-extraction-status");
  const button = document.querySelector("#run-current-extraction");
  const saveButton = document.querySelector("#save-current-snapshot");
  const packetLink = document.querySelector("#open-current-packet");
  if (!status || !el || !button || !packetLink || !saveButton) return;
  const lead = status.supported ? "自动抽取规则已可用" : status.packet_ready ? "抽取包已生成" : status.action === "prepare_packet" ? "可生成抽取包" : "当前需人工处理";
  const packetNote = status.packet_ready ? ` · 抽取包可查看` : "";
  const savedNote = status.saved_snapshot_path ? ` · 最近 CSV 备份：${status.saved_snapshot_path}` : "";
  const scanNote = status.scanned
    ? `已入库 ${status.row_count} 条数据${status.completed_ai_run_count ? `；DeepSeek 已完成 ${status.completed_ai_run_count} 次` : ""}`
    : "尚无已入库抽取结果";
  el.textContent = `${lead} · ${scanNote}${packetNote}${savedNote} · ${status.message}`;
  el.className = status.supported ? "supported" : status.packet_ready ? "ready" : "unsupported";
  button.disabled = status.action === "manual_only";
  button.textContent = status.action === "prepare_packet" ? "生成抽取包" : "自动提取/核验";
  saveButton.disabled = !state.rows.length;
  packetLink.hidden = !status.packet_ready;
  if (status.packet_ready) {
    packetLink.href = status.packet_url;
  } else {
    packetLink.removeAttribute("href");
  }
}

function renderEvidenceAudit() {
  const audit = state.audit;
  const el = document.querySelector("#paper-evidence-audit");
  if (!el) return;
  if (!audit) {
    el.textContent = "";
    el.className = "";
    return;
  }
  const coverage = Math.round((audit.coverage_ratio || 0) * 100);
  const strong = Math.round((audit.strong_ratio || 0) * 100);
  el.textContent = `证据覆盖：${coverage}% 可高亮定位，${strong}% 为句子/片段级强定位。${audit.message}`;
  el.className = audit.failed_rows ? "audit-warning" : "audit-ok";
}

function renderDeepSeekRun() {
  const el = document.querySelector("#paper-deepseek-status");
  const button = document.querySelector("#run-deepseek-preview");
  if (!el || !button) return;
  button.textContent = state.rows.length ? "DeepSeek 补充抽取（不覆盖）" : "DeepSeek 自动抽取";
  button.disabled = !state.ai?.configured || !state.paper?.pdf_path;
  if (!state.ai?.configured) {
    el.textContent = "DeepSeek 未配置：上传、去重、检索和人工校对仍可使用。";
    el.className = "unsupported";
    return;
  }
  if (!state.deepseekRun) {
    el.textContent = state.rows.length
      ? "当前文章已有入库数据；补充抽取只生成候选结果，不覆盖已校对内容。"
      : "尚无 DeepSeek 抽取记录；自动抽取结果需经证据核验后进入校对表。";
    el.className = "ready";
    return;
  }
  const run = state.deepseekRun;
  const link = run.output_url ? ` · <a href="${esc(run.output_url)}" target="_blank" rel="noopener">查看运行 JSON</a>` : "";
  el.innerHTML = `DeepSeek ${esc(run.status)} · 候选 ${esc(run.candidate_count)} 条 · 双重验证通过 ${esc(run.verified_count)} 条 · 重复 ${esc(run.duplicate_count || 0)} 条 · 拒绝/歧义 ${esc(run.rejected_count)} 条${link}`;
  el.className = run.status === "completed" ? "supported" : run.status === "failed" ? "unsupported" : "ready";
}

function updateProgress(value, text, running = true) {
  const panel = document.querySelector("#deepseek-progress");
  const bar = document.querySelector("#deepseek-progress-bar");
  const label = document.querySelector("#deepseek-progress-text");
  if (!panel || !bar || !label) return;
  panel.hidden = false;
  panel.classList.toggle("running", running);
  state.progressValue = Math.max(0, Math.min(100, value));
  bar.style.width = `${state.progressValue}%`;
  label.textContent = text;
}

function startProgress(kind) {
  clearInterval(state.progressTimer);
  const stages = [
    "准备任务与读取 PDF",
    "按页分块提取候选数据",
    "核对证据页码与原文片段",
    "合并重复项并生成可校对结果",
  ];
  let index = 0;
  updateProgress(12, `${kind}：${stages[index]}`);
  state.progressTimer = setInterval(() => {
    index = Math.min(index + 1, stages.length - 1);
    const next = Math.min(88, state.progressValue + 18);
    updateProgress(next, `${kind}：${stages[index]}`);
  }, 3500);
}

function finishProgress(text, ok = true) {
  clearInterval(state.progressTimer);
  state.progressTimer = null;
  updateProgress(ok ? 100 : Math.max(state.progressValue, 92), text, false);
}

function confirmRescanIfNeeded(actionLabel) {
  const status = state.extraction || {};
  const alreadyScanned = Boolean(status.scanned || state.rows.length || state.deepseekRun?.status === "completed");
  if (!alreadyScanned) return false;
  const rows = status.row_count ?? state.rows.length;
  const completedRuns = status.completed_ai_run_count ?? (state.deepseekRun?.status === "completed" ? 1 : 0);
  const detail = [
    rows ? `本地已有 ${rows} 条六列数据` : "",
    completedRuns ? `DeepSeek 已完成运行 ${completedRuns} 次` : "",
  ].filter(Boolean).join("；");
  return window.confirm(
    `这篇文章已经扫描过${detail ? `（${detail}）` : ""}。\n\n` +
    `再次执行“${actionLabel}”会重新调用 DeepSeek/自动抽取，可能产生新的费用和新的候选结果。\n\n` +
    "只有确认仍然需要再次扫描，才会继续。是否继续？"
  );
}

async function runDeepSeekPreview() {
  if (!confirmDiscardUnsaved("DeepSeek 补充抽取")) return;
  const button = document.querySelector("#run-deepseek-preview");
  const forceRescan = confirmRescanIfNeeded("DeepSeek 补充抽取");
  if ((state.extraction?.scanned || state.rows.length || state.deepseekRun?.status === "completed") && !forceRescan) {
    toast("已取消再次扫描；当前仍显示本地已保存数据。");
    return;
  }
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "DeepSeek 处理中…";
  startProgress("DeepSeek 抽取");
  try {
    const result = await api("/api/current-paper/deepseek-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, commit: state.rows.length === 0, chunk_pages: 2, force_rescan: forceRescan }),
    });
    state.deepseekRun = {
      ...result,
      status: "completed",
      output_url: `/api/deepseek-runs/${result.run_id}.json`,
    };
    if (result.imported?.inserted) await loadCurrentPaper();
    renderDeepSeekRun();
    finishProgress(`完成：候选 ${result.candidate_count} 条，证据核验通过 ${result.verified_count} 条。`);
    toast(`DeepSeek 抽取完成：候选 ${result.candidate_count} 条，双重验证通过 ${result.verified_count} 条。`);
  } catch (error) {
    finishProgress(`处理失败：${error.message}`, false);
    toast(error.message, true);
    await loadCurrentPaper();
  } finally {
    button.disabled = false;
    button.textContent = previous;
    renderDeepSeekRun();
  }
}

function filteredRows() {
  const q = state.filter.trim().toLowerCase();
  let rows = state.rows;
  if (state.reviewFilter === "unreviewed") {
    rows = rows.filter(row => row.origin_type !== "manual" && row.version_no === 0);
  } else if (state.reviewFilter === "confirmed") {
    rows = rows.filter(row => row.review_action === "confirmation");
  } else if (state.reviewFilter === "corrected") {
    rows = rows.filter(row => row.review_action === "correction");
  } else if (state.reviewFilter === "manual") {
    rows = rows.filter(row => row.origin_type === "manual");
  }
  if (!q) return rows;
  return rows.filter(row => fields.some(field => String(row[field] || "").toLowerCase().includes(q)));
}

function reviewProgress() {
  const total = state.rows.length;
  const manual = state.rows.filter(row => row.origin_type === "manual").length;
  const confirmed = state.rows.filter(row => row.review_action === "confirmation").length;
  const corrected = state.rows.filter(row => row.review_action === "correction").length;
  const reviewed = manual + confirmed + corrected;
  return { total, reviewed, unreviewed: Math.max(total - reviewed, 0), confirmed, corrected, manual };
}

function isUnreviewedRow(row) {
  return row.origin_type !== "manual" && Number(row.version_no) === 0;
}

function renderTable() {
  const rows = filteredRows();
  const progress = reviewProgress();
  const filterNote = state.reviewFilter === "all" ? "" : ` · 当前筛出 ${rows.length} 条`;
  const dirtyNote = hasUnsavedEdits() ? `，${state.dirtyRows.size} 行未确认` : "";
  const breakdown = `确认 ${progress.confirmed}、修正 ${progress.corrected}、人工 ${progress.manual}`;
  setText("row-count", `${progress.reviewed}/${progress.total} 已审核（${breakdown}），${progress.unreviewed} 待审核${dirtyNote}${filterNote}`);
  const body = document.querySelector("#edit-rows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty-table">当前筛选条件下没有数据。你可以切回“全部”或“只看未审核”。</div></td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => {
    const cls = [state.selected === row.item_id ? "selected" : "", state.dirtyRows.has(Number(row.item_id)) ? "dirty" : "", row.review_action === "confirmation" ? "confirmed" : "", row.version_no > 0 && row.review_action !== "confirmation" ? "revised" : "", row.origin_type === "manual" ? "manual" : ""].filter(Boolean).join(" ");
    const cells = fields.map(field => `<td><textarea class="cell ${field === "context_explanation" ? "context" : ""}" data-field="${field}" aria-label="${fieldLabels[field]}">${esc(row[field])}</textarea></td>`).join("");
    const badge = row.origin_type === "manual" ? "人工" : row.review_action === "confirmation" ? `已确认 v${row.version_no}` : row.version_no > 0 ? `已修正 v${row.version_no}` : "未审核";
    return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action"><button class="confirm" data-confirm="${row.item_id}">确认当前内容</button><button class="confirm-next" data-confirm-next="${row.item_id}">确认并下一条</button><button data-original="${row.item_id}">查看原始</button><small>${badge}</small></div></td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-item]").forEach(tr => tr.addEventListener("click", event => {
    if (event.target.closest("button[data-confirm],button[data-confirm-next]")) return;
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
  body.querySelectorAll("[data-confirm-next]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    confirmRow(Number(btn.dataset.confirmNext), { goNext: true });
  }));
  body.querySelectorAll("[data-field]").forEach(input => input.addEventListener("input", event => {
    const tr = event.target.closest("tr[data-item]");
    const id = Number(tr?.dataset.item);
    const row = state.rows.find(item => item.item_id === id);
    if (!row) return;
    const dirty = fields.some(field => String(collectRowFields(id)[field] ?? "") !== String(row[field] ?? ""));
    markRowDirty(id, dirty);
  }));
}

function selectRow(id) {
  state.selected = id;
  const row = state.rows.find(item => item.item_id === id);
  renderOriginal(row);
  document.querySelectorAll("#edit-rows tr[data-item]").forEach(tr => tr.classList.toggle("selected", Number(tr.dataset.item) === id));
}

function selectNextUnreviewed() {
  let candidates = filteredRows().filter(isUnreviewedRow);
  if (!candidates.length && state.reviewFilter !== "unreviewed") {
    if (hasUnsavedEdits() && !confirmDiscardUnsaved("切换到只看未审核")) return;
    state.reviewFilter = "unreviewed";
    const filter = document.querySelector("#review-filter");
    if (filter) filter.value = "unreviewed";
    renderTable();
    candidates = filteredRows().filter(isUnreviewedRow);
  }
  if (!candidates.length) {
    toast("当前筛选条件下没有未审核数据。");
    return;
  }
  const currentIndex = candidates.findIndex(row => Number(row.item_id) === Number(state.selected));
  const next = candidates[currentIndex >= 0 ? (currentIndex + 1) % candidates.length : 0];
  selectRow(Number(next.item_id));
  document.querySelector(`#edit-rows tr[data-item="${next.item_id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function confirmSelectedRow(options = {}) {
  if (!state.selected) {
    toast("请先选择一条数据。", true);
    return;
  }
  confirmRow(Number(state.selected), options);
}

function handleReviewKeyboard(event) {
  if (document.body.dataset.view !== "review") return;
  const key = event.key.toLowerCase();
  const commandOrCtrl = event.metaKey || event.ctrlKey;
  if (event.key === "Enter" && commandOrCtrl) {
    event.preventDefault();
    confirmSelectedRow({ goNext: event.shiftKey });
  } else if (key === "n" && event.altKey && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    selectNextUnreviewed();
  }
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
  return `<article class="source-meta-card"><strong>正在定位</strong><p>${esc(row.original_meaning || row.meaning)}<br>PDF第 ${esc(row.original_source_page || row.source_page || "?")} 页 · ${esc(row.original_source_locator || row.source_locator || "未标注")}</p></article><article class="source-meta-card"><strong>准备内容</strong><p>将优先展示高亮句子的放大图，再保留整页位置供你核对上下文。</p></article>`;
}

function sourceMetaHtml(data) {
  const matched = data.matched_text
    ? `<article class="source-meta-card emphasis"><strong>高亮句子</strong><p>${esc(data.matched_text)}</p></article>`
    : "";
  return `<article class="source-meta-card"><strong>${esc(data.match_label)}</strong><p>PDF第 ${esc(data.page_number)} 页 · ${esc(data.locator || "未标注")}<br>${esc(data.match_note)}</p></article>${matched}<article class="source-meta-card"><strong>证据片段</strong><p>${esc(data.excerpt || "未保留原始证据片段")}</p></article>`;
}

async function openSourceViewer(id) {
  const row = state.rows.find(item => item.item_id === id);
  if (!row) return;
  const dialog = document.querySelector("#source-dialog");
  const meta = document.querySelector("#source-meta");
  const focusImage = document.querySelector("#source-focus-image");
  const image = document.querySelector("#source-image");
  const pdfLink = document.querySelector("#source-open-pdf");
  meta.innerHTML = sourceMetaLoading(row);
  focusImage.removeAttribute("src");
  focusImage.alt = "正在加载高亮句子放大图";
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
    const ts = Date.now();
    focusImage.src = `${data.snippet_url}?ts=${ts}`;
    focusImage.alt = data.has_highlight
      ? `${data.match_label}：对应句子放大图`
      : "当前暂无精确高亮，显示整页区域";
    image.src = `${data.image_url}?ts=${ts}`;
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

async function confirmRow(id, options = {}) {
  try {
    const values = collectRowFields(id);
    const current = state.rows.find(row => row.item_id === id);
    const changed = fields.filter(field => String(values[field] ?? "") !== String(current[field] ?? ""));
    const result = await api(`/api/six-data/${id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fields: values,
        editor: "本地研究者",
        note: changed.length ? `修改字段：${changed.map(field => fieldLabels[field]).join("、")}` : "人工确认：内容无修改",
      }),
    });
    state.rows = state.rows.map(row => row.item_id === id ? result : row);
    clearDirtyRows(id);
    state.learning = await api(`/api/current-paper/learning-samples?paper_id=${encodeURIComponent(state.paper.id)}`);
    state.allLearning = await api("/api/learning-samples");
    state.audit = await api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(state.paper.id)}`);
    state.selected = id;
    renderTable();
    renderOriginal(result);
    renderEvidenceAudit();
    renderHistory();
    if (options.goNext) {
      window.requestAnimationFrame(() => selectNextUnreviewed());
    }
    toast(result.review_action === "confirmation"
      ? `已确认内容无误并记录为 v${result.version_no}；自动提取原始版本未改变。`
      : `已保存修正并创建版本 v${result.version_no}；自动提取原始版本未改变。`);
  } catch (error) {
    toast(error.message, true);
  }
}

function fillManualDefaults() {
  const form = document.querySelector("#manual-form");
  const paper = selectedManualPaper();
  if (!form || !paper) return;
  form.elements.article_title.value = paper.title || "";
  form.elements.doi.value = paper.doi || "";
}

function selectedManualPaper() {
  const select = document.querySelector("#manual-paper-select");
  const selectedId = Number(select?.value || state.paper?.id || 0);
  return state.papers.find(paper => Number(paper.id) === selectedId) || state.paper;
}

async function saveManual(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const paper = selectedManualPaper();
  if (!paper?.id) {
    toast("请先选择人工数据所属文章。", true);
    return;
  }
  const targetPaperId = Number(paper.id);
  const values = {};
  fields.forEach(field => {
    values[field] = form.elements[field].value;
  });
  try {
    const result = await api("/api/six-data/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: targetPaperId, fields: values, editor: "本地研究者" }),
    });
    if (state.paper && targetPaperId === Number(state.paper.id)) {
      state.rows.push(result);
      state.learning = await api(`/api/current-paper/learning-samples?paper_id=${encodeURIComponent(state.paper.id)}`);
      state.audit = await api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(state.paper.id)}`);
      setText("nav-count", state.rows.length);
    }
    [state.papers, state.allLearning] = await Promise.all([
      api("/api/papers"),
      api("/api/learning-samples"),
    ]);
    form.reset();
    renderPaperOptions();
    document.querySelector("#manual-paper-select").value = String(targetPaperId);
    fillManualDefaults();
    if (state.paper && targetPaperId === Number(state.paper.id)) {
      renderTable();
      renderEvidenceAudit();
    }
    renderHistory();
    toast(`人工数据已保存到《${paper.title || "所选文章"}》；未调用 DeepSeek。`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function importJsonResult(event) {
  event.preventDefault();
  if (!confirmDiscardUnsaved("导入 JSON 并刷新当前表格")) return;
  const textarea = document.querySelector("#import-json-text");
  const jsonText = textarea.value.trim();
  if (!jsonText) {
    toast("请先粘贴抽取结果 JSON。", true);
    return;
  }
  try {
    const result = await api("/api/current-paper/import-json", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, json_text: jsonText }),
    });
    textarea.value = "";
    await loadCurrentPaper();
    toast(`JSON 导入完成：新增 ${result.inserted} 条，已存在 ${result.existing} 条。`);
    switchView("review");
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
    document.querySelector("#search-export-xlsx").href = `/api/six-export.xlsx?q=${encodeURIComponent(q)}`;
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
  el.innerHTML = rows.map(row => {
    const authors = [row.first_author ? `一作：${row.first_author}` : "", row.corresponding_author ? `通讯：${row.corresponding_author}` : ""].filter(Boolean).join(" · ");
    const paperLine = [row.article_title, row.doi, authors].filter(Boolean).join(" · ");
    return `<article class="result"><div class="value">${esc(row.value_text)}<small> ${esc(row.unit)}</small></div><strong>${esc(row.meaning)}</strong><em>${row.search_score != null ? `相关度 ${esc(row.search_score)}` : ""}</em><p>${esc(row.context_explanation)}</p><div class="result-paper">${esc(paperLine)}</div><button class="row-link" data-jump="${row.item_id}">去校对</button></article>`;
  }).join("");
  el.querySelectorAll("[data-jump]").forEach(btn => btn.addEventListener("click", () => jumpToRow(Number(btn.dataset.jump))));
}

async function jumpToRow(id) {
  try {
    let row = state.rows.find(item => item.item_id === id);
    if (!row) {
      const item = await api(`/api/six-data/${id}`);
      if (item.paper_id !== state.paper.id) {
        const switched = await switchCurrentPaper({ paperId: item.paper_id, silent: true });
        if (!switched) return;
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
  const summary = document.querySelector("#learning-summary");
  const exportLink = document.querySelector("#learning-export");
  const exportAllLink = document.querySelector("#learning-export-all");
  if (summary && exportLink && exportAllLink) {
    const learning = state.learning || { sample_count: 0, correction_count: 0, confirmation_count: 0, manual_count: 0 };
    const allLearning = state.allLearning || { sample_count: 0 };
    summary.textContent = learning.sample_count
      ? `当前文章已有 ${learning.sample_count} 条学习样本：确认无误 ${learning.confirmation_count} 条，修正 ${learning.correction_count} 条，人工补录 ${learning.manual_count} 条。全库共 ${allLearning.sample_count || 0} 条。`
      : `当前文章还没有学习样本；全库共 ${allLearning.sample_count || 0} 条。确认修正或人工补录后，这里会自动累积。`;
    exportLink.href = `/api/current-paper/learning-samples.jsonl?paper_id=${encodeURIComponent(state.paper?.id || "")}`;
    exportLink.classList.toggle("disabled", !learning.sample_count);
    exportLink.setAttribute("aria-disabled", learning.sample_count ? "false" : "true");
    exportAllLink.href = "/api/learning-samples.jsonl";
    exportAllLink.classList.toggle("disabled", !allLearning.sample_count);
    exportAllLink.setAttribute("aria-disabled", allLearning.sample_count ? "false" : "true");
  }
  if (!changed.length) {
    el.innerHTML = '<div class="blank"><h3>还没有已确认修正</h3><p>左侧表格里的临时输入不会出现在这里。</p></div>';
    return;
  }
  el.innerHTML = changed.map(row => `<article class="history-card"><span>${row.origin_type === "manual" ? "人工补录" : row.review_action === "confirmation" ? `确认无误 v${row.version_no}` : `已修正 v${row.version_no}`}</span><div><strong>${esc(row.meaning)} · ${esc(row.value_text)} ${esc(row.unit)}</strong><p>${esc(row.context_explanation)}</p></div><div><strong>${esc(row.editor)}</strong><p>${esc(row.edit_note || "")}<br>${esc(row.created_at)}</p></div></article>`).join("");
}

function switchView(name) {
  document.querySelectorAll(".nav,.view").forEach(el => el.classList.remove("active"));
  document.querySelector(`.nav[data-view="${name}"]`).classList.add("active");
  document.querySelector(`#view-${name}`).classList.add("active");
  document.body.dataset.view = name;
  if (name === "search" && !document.querySelector("#search-results").children.length) runSearch();
  if (name === "upload") refreshUploadWorkspace();
  if (name === "manual") fillManualDefaults();
}

async function refreshUploadWorkspace() {
  try {
    [state.uploads, state.jobs, state.ai] = await Promise.all([
      api("/api/uploads"), api("/api/processing-jobs"), api("/api/ai/status"),
    ]);
    renderUploadWorkspace();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderUploadWorkspace() {
  const ai = document.querySelector("#deepseek-status");
  if (ai && state.ai) {
    ai.textContent = state.ai.configured
      ? `DeepSeek 已配置 · ${state.ai.extraction_model}`
      : "DeepSeek 待配置 · 上传与去重可正常使用";
    ai.className = `ai-state ${state.ai.configured ? "configured" : "pending"}`;
  }
  const queue = document.querySelector("#processing-queue");
  if (queue) {
    queue.innerHTML = state.jobs.length ? state.jobs.map(job => {
      const labels = { extract: "等待抽取", ocr: "等待 OCR", duplicate_review: "重复版本核对" };
      const statuses = { queued: "排队中", running: "处理中", blocked: "等待人工", completed: "已完成", failed: "失败", cancelled: "已取消" };
      return `<article class="queue-card"><span class="queue-kind ${esc(job.status)}">${esc(labels[job.job_type] || job.job_type)}</span><div><strong>${esc(job.paper_title)}</strong><p>${esc(job.original_filename || "")}</p></div><div><strong>${esc(statuses[job.status] || job.status)}</strong><p>${esc(job.message || "")} · ${esc(job.provider)}</p></div></article>`;
    }).join("") : '<div class="empty-queue">暂无处理任务。</div>';
  }
  const events = document.querySelector("#upload-events");
  if (events) {
    events.innerHTML = state.uploads.length ? state.uploads.map(item => {
      const outcome = item.outcome === "accepted" ? "新文献" : item.outcome === "duplicate" ? "重复" : "已拒绝";
      return `<article class="queue-card"><span class="queue-kind ${esc(item.outcome)}">${outcome}</span><div><strong>${esc(item.original_filename)}</strong><p>${esc(item.matched_paper_title || item.details?.reason || "")}</p></div><div><strong>${esc(item.match_type || "validation")}</strong><p>${esc(item.created_at)}</p></div></article>`;
    }).join("") : '<div class="empty-queue">还没有上传记录。</div>';
  }
}

function renderUploadResult(result) {
  const box = document.querySelector("#upload-result");
  const duplicate = result.outcome === "duplicate";
  const details = duplicate
    ? `匹配类型：${esc(result.match_type)}${result.similarity != null ? ` · 相似度 ${esc(Math.round(result.similarity * 100))}%` : ""}`
    : `${esc(result.page_count)} 页 · 提取文字 ${esc(result.text_char_count)} 字符${result.needs_ocr ? " · 需要 OCR" : ""}`;
  box.innerHTML = `<div class="upload-result-card ${duplicate ? "duplicate" : "accepted"}"><span>${duplicate ? "DUPLICATE" : "ACCEPTED"}</span><h3>${esc(result.matched_title || result.title || "上传结果")}</h3><p>${esc(result.message)}</p><small>${details}</small><button type="button" data-open-upload-paper="${esc(result.paper_id)}">切换到这篇文章</button></div>`;
  box.querySelector("[data-open-upload-paper]")?.addEventListener("click", async () => {
    const switched = await switchCurrentPaper({ paperId: result.paper_id });
    if (!switched) return;
    switchView("review");
  });
}

async function uploadPdf(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.pdf.files[0];
  if (!file) {
    toast("请先选择 PDF 文件。", true);
    return;
  }
  const button = document.querySelector("#pdf-upload-submit");
  const previous = button.textContent;
  const params = new URLSearchParams({ filename: file.name });
  ["title", "doi", "year", "first_author", "corresponding_author"].forEach(name => {
    const value = form.elements[name].value.trim();
    if (value) params.set(name, value);
  });
  button.disabled = true;
  button.textContent = "正在验证与去重…";
  try {
    const result = await api(`/api/uploads/pdf?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
    renderUploadResult(result);
    state.papers = await api("/api/papers");
    renderPaperOptions();
    await refreshUploadWorkspace();
    toast(result.message, result.outcome === "rejected");
  } catch (error) {
    document.querySelector("#upload-result").innerHTML = `<div class="upload-result-card rejected"><span>REJECTED</span><h3>上传未通过</h3><p>${esc(error.message)}</p></div>`;
    await refreshUploadWorkspace();
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

async function switchCurrentPaper({ articleKey = null, paperId = null, silent = false } = {}) {
  if (!confirmDiscardUnsaved("切换文章")) return false;
  const payload = {};
  if (paperId != null) payload.paper_id = paperId;
  if (articleKey != null) payload.article_key = articleKey;
  await api("/api/current-paper", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await loadCurrentPaper();
  if (!silent) toast(`已切换到《${state.paper.title || "未命名文章"}》。`);
  return true;
}

async function runCurrentExtraction() {
  if (!confirmDiscardUnsaved("自动提取/核验")) return;
  const button = document.querySelector("#run-current-extraction");
  const status = state.extraction;
  if (!status || status.action === "manual_only") {
    toast(status?.message || "这篇文章当前还不能自动处理。", true);
    return;
  }
  const forceRescan = confirmRescanIfNeeded(status.action_label || "当前文章自动提取");
  if (status.scanned && !forceRescan) {
    toast("已取消再次扫描；当前仍显示本地已保存数据。");
    return;
  }
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "处理中…";
  startProgress("自动提取/核验");
  try {
    const result = await api("/api/current-paper/run-workflow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, force_rescan: forceRescan }),
    });
    await loadCurrentPaper();
    if (result.action === "prepare_packet") {
      finishProgress("抽取包已生成，等待导入结构化结果。");
      toast(`当前文章已切换，抽取包已生成：${result.action_result?.packet_path || "默认目录"}。`);
    } else if (result.action === "deepseek_extract" || result.action === "deepseek_preview") {
      finishProgress(`完成：候选 ${result.action_result?.candidate_count ?? 0} 条，证据核验通过 ${result.action_result?.verified_count ?? 0} 条。`);
      toast(`DeepSeek 处理完成：候选 ${result.action_result?.candidate_count ?? 0} 条，双重验证通过 ${result.action_result?.verified_count ?? 0} 条。`);
    } else {
      const extraction = result.action_result?.extraction || {};
      finishProgress(`完成：新增 ${extraction.inserted ?? 0} 条，已存在 ${extraction.existing ?? 0} 条。`);
      toast(`自动提取完成：新增 ${extraction.inserted ?? 0} 条，已存在 ${extraction.existing ?? 0} 条。`);
    }
  } catch (error) {
    finishProgress(`处理失败：${error.message}`, false);
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
    renderExtractionStatus();
  }
}

async function saveCurrentSnapshot() {
  if (!state.paper) return;
  if (hasUnsavedEdits()) {
    toast("仍有未确认修改；CSV 备份只包含已写入数据库的数据。请先逐行确认。", true);
    return;
  }
  if (!state.rows.length) {
    toast("当前文章没有可备份的数据。", true);
    return;
  }
  const button = document.querySelector("#save-current-snapshot");
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const result = await api("/api/current-paper/save-snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id }),
    });
    state.extraction = await api(`/api/current-paper/extraction?paper_id=${encodeURIComponent(state.paper.id)}`);
    renderExtractionStatus();
    toast(`已生成 ${result.row_count} 条当前文章 CSV 备份：${result.path}`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
    renderExtractionStatus();
  }
}

async function submitPaperSwitch(event) {
  event.preventDefault();
  const input = document.querySelector("#paper-switch-input");
  const button = document.querySelector("#paper-switch-submit");
  const paperId = Number(input.value);
  if (!paperId) {
    toast("请先选择一篇文章。", true);
    return;
  }
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "正在切换…";
  try {
    const switched = await switchCurrentPaper({ paperId, silent: true });
    if (!switched) return;
    toast(`已切换到《${state.paper.title || "未命名文章"}》。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

document.querySelectorAll(".nav").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
document.querySelector("#table-filter").addEventListener("input", event => {
  state.filter = event.target.value;
  renderTable();
});
document.querySelector("#review-filter").addEventListener("change", event => {
  state.reviewFilter = event.target.value;
  state.selected = null;
  renderTable();
  renderOriginalPlaceholder();
});
document.querySelector("#next-unreviewed").addEventListener("click", selectNextUnreviewed);
document.addEventListener("keydown", handleReviewKeyboard);
document.querySelector("#search-form").addEventListener("submit", runSearch);
document.querySelector("#manual-form").addEventListener("submit", saveManual);
document.querySelector("#manual-paper-select").addEventListener("change", fillManualDefaults);
document.querySelector("#import-json-form").addEventListener("submit", importJsonResult);
document.querySelector("#pdf-upload-form").addEventListener("submit", uploadPdf);
document.querySelector("#pdf-upload-file").addEventListener("change", event => {
  const file = event.target.files[0];
  setText("upload-file-label", file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "点击选择本地 PDF");
});
document.querySelector("#refresh-upload-queue").addEventListener("click", refreshUploadWorkspace);
document.querySelector("#paper-switch-form").addEventListener("submit", submitPaperSwitch);
document.querySelector("#run-current-extraction").addEventListener("click", runCurrentExtraction);
document.querySelector("#run-deepseek-preview").addEventListener("click", runDeepSeekPreview);
document.querySelector("#save-current-snapshot").addEventListener("click", saveCurrentSnapshot);
document.querySelector("[data-close-source]")?.addEventListener("click", () => document.querySelector("#source-dialog")?.close());
document.querySelector("#source-dialog")?.addEventListener("click", event => {
  const dialog = event.currentTarget;
  if (event.target === dialog) dialog.close();
});
window.addEventListener("beforeunload", event => {
  if (!hasUnsavedEdits()) return;
  event.preventDefault();
  event.returnValue = "";
});
load().catch(error => toast(error.message, true));
