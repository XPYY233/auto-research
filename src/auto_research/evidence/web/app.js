const state = { paper: null, papers: [], testSet: null, paperFilters: { query: "", author: "", topic: "all", status: "all", scope: "all" }, rows: [], selected: null, filter: "", reviewFilter: "all", reviewSort: "review_priority", reviewVisibleLimit: 80, reviewObject: "data", visualAssets: [], cloudCandidates: [], cloudRuns: [], cloudQuality: null, visualMode: "legacy", mineru: null, cloudProgressTimer: null, calibrationReviewIds: new Set(), calibrationActive: false, calibrationBatchTotal: 0, reviewNotes: new Map(), fieldDirtyRows: new Set(), search: "", searchMode: "item", searchFilters: { review: "all", source: "all", sort: "relevance" }, searchResults: [], searchRequest: 0, searchComposing: false, visualAsset: null, extraction: null, experimentProfile: null, learning: null, allLearning: null, learningReport: null, allLearningReport: null, audit: null, deepseekRun: null, uploads: [], jobs: [], ai: null, uiMode: { read_only: false }, runtimeWarnings: new Map(), dirtyRows: new Set(), progressTimer: null, progressValue: 0, focusReview: false, reviewDecision: null };
const fields = ["value_text", "meaning", "unit", "article_title", "doi", "context_explanation"];
const viewCopy = {
  review: { kicker: "EVIDENCE REVIEW", title: "校对实验数据", subtitle: "逐条核对抽取结果，并随时返回原文证据。" },
  search: { kicker: "DATABASE SEARCH", title: "搜索实验数据与证据", subtitle: "在数值事实、原始表格、论文图片和定性结论之间切换。" },
  upload: { kicker: "PDF INTAKE", title: "导入实验文献", subtitle: "验证真实 PDF、识别重复论文，并加入待处理队列。" },
  manual: { kicker: "MANUAL ENTRY", title: "补录遗漏数据", subtitle: "为自动抽取未覆盖的实验结果补充六列记录。" },
  history: { kicker: "REVISION HISTORY", title: "查看修正记录", subtitle: "复查人工确认、修正和补录留下的版本记录。" },
};
const fieldLabels = {
  value_text: "具体数值",
  meaning: "具体意义",
  unit: "单位",
  article_title: "文章题目",
  doi: "DOI",
  context_explanation: "数据在文中的解释",
};
const calibrationStoragePrefix = "evidence-calibration-batch-v1:";
const recentPapersStorageKey = "evidence-recent-papers-v1";
const reviewPageSize = 80;

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `请求失败 ${response.status}`);
  return body;
}

function renderRuntimeWarnings() {
  const el = document.querySelector("#runtime-warning");
  if (!el) return;
  const warnings = [...state.runtimeWarnings.values()];
  el.hidden = !warnings.length;
  el.textContent = warnings.length ? `部分功能未载入 · ${warnings.length}` : "";
  el.title = warnings.join("\n");
}

async function apiOptional(url, fallback, label) {
  try {
    const result = await api(url);
    state.runtimeWarnings.delete(label);
    renderRuntimeWarnings();
    return result;
  } catch (error) {
    state.runtimeWarnings.set(label, `${label}：${error.message}`);
    renderRuntimeWarnings();
    return fallback;
  }
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

function isReadOnly() {
  return Boolean(state.uiMode?.read_only);
}

function applyUiMode() {
  const readonly = isReadOnly();
  document.body.dataset.readonly = readonly ? "true" : "false";
  const badge = document.querySelector("#readonly-badge");
  if (badge) badge.hidden = !readonly;
  const release = document.querySelector("#release-badge span");
  if (release) release.textContent = state.uiMode?.release?.label || "本地版本";
  const releaseBadge = document.querySelector("#release-badge");
  if (releaseBadge) releaseBadge.title = [
    state.uiMode?.release?.version,
    state.uiMode?.release?.evidence_schema ? `证据库结构 v${state.uiMode.release.evidence_schema}` : "",
  ].filter(Boolean).join(" · ");
  document.querySelectorAll('[data-view="upload"],[data-view="manual"],[data-write-action]').forEach(el => {
    el.hidden = readonly;
  });
  document.querySelectorAll(".nav").forEach(btn => {
    btn.hidden = readonly && btn.dataset.view !== "search";
  });
  document.querySelectorAll(".article-mini,.save-rule,.article-picker,.article-strip").forEach(el => {
    el.hidden = readonly;
  });
}

function renderPublicSearchOnlyMode() {
  document.querySelector(".brand strong").textContent = "实验数据检索库";
  document.querySelector(".brand small").textContent = "READ-ONLY SEARCH";
  const searchSummary = document.querySelector("#search-summary");
  if (searchSummary) searchSummary.textContent = "只读模式：仅支持搜索、原文证据查看和导出";
}

function renderViewHeader(name) {
  const copy = viewCopy[name] || viewCopy.review;
  setText("workspace-kicker", copy.kicker);
  setText("workspace-title", copy.title);
  setText(
    "workspace-subtitle",
    isReadOnly() && name === "search"
      ? "只读浏览已提取数据；支持原文证据查看和结果导出。"
      : copy.subtitle,
  );
}

function rejectReadOnlyAction(action = "修改数据") {
  if (!isReadOnly()) return false;
  toast(`只读模式仅支持浏览、搜索、查看原文证据和导出；不能${action}。`, true);
  return true;
}

function setText(id, value) {
  document.getElementById(id).textContent = value ?? "";
}

function hasUnsavedEdits() {
  return state.dirtyRows.size > 0;
}

function clearDirtyRows(itemId = null) {
  if (itemId == null) {
    state.dirtyRows.clear();
    state.reviewNotes.clear();
    state.fieldDirtyRows.clear();
  } else {
    state.dirtyRows.delete(Number(itemId));
    state.reviewNotes.delete(Number(itemId));
    state.fieldDirtyRows.delete(Number(itemId));
  }
}

function confirmDiscardUnsaved(actionLabel) {
  if (!hasUnsavedEdits()) return true;
  return window.confirm(
    `当前有 ${state.dirtyRows.size} 行修改或核验备注尚未确认。\n\n` +
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
    paper?.first_author ? paper.first_author : "",
    paper?.year ? `${paper.year}` : "",
    paper?.doi ? paper.doi : "",
  ].filter(Boolean);
  return details.length ? `${title} · ${details.join(" · ")}` : title;
}

function calibrationStorageKey(paperId) {
  return `${calibrationStoragePrefix}${Number(paperId)}`;
}

function readSavedCalibrationBatch(paperId) {
  if (!paperId) return [];
  try {
    const payload = JSON.parse(window.localStorage.getItem(calibrationStorageKey(paperId)) || "null");
    if (!payload || Number(payload.paper_id) !== Number(paperId) || !Array.isArray(payload.item_ids)) return [];
    return [...new Set(payload.item_ids.map(Number).filter(Number.isInteger))];
  } catch (_error) {
    return [];
  }
}

function saveCalibrationBatch(paperId, itemIds) {
  if (!paperId || !itemIds.length) return;
  try {
    window.localStorage.setItem(calibrationStorageKey(paperId), JSON.stringify({
      paper_id: Number(paperId),
      item_ids: itemIds.map(Number),
      saved_at: new Date().toISOString(),
    }));
  } catch (_error) {
    // The review still works when private browsing disables local storage.
  }
}

function clearSavedCalibrationBatch(paperId) {
  if (!paperId) return;
  try {
    window.localStorage.removeItem(calibrationStorageKey(paperId));
  } catch (_error) {
    // Nothing else is required when storage is unavailable.
  }
}

function restoreCalibrationBatch() {
  const rowsById = new Map(state.rows.map(row => [Number(row.item_id), row]));
  const saved = readSavedCalibrationBatch(state.paper?.id).filter(id => rowsById.has(id));
  const remaining = saved.filter(id => isUnreviewedRow(rowsById.get(id)));
  if (!remaining.length) {
    clearSavedCalibrationBatch(state.paper?.id);
    state.calibrationReviewIds = new Set();
    state.calibrationBatchTotal = 0;
    return;
  }
  state.calibrationReviewIds = new Set(saved);
  state.calibrationBatchTotal = saved.length;
}

function renderOriginalPlaceholder() {
  document.querySelector("#original-pane").innerHTML = '<div class="blank"><span>↖</span><h3>选择一条数据</h3><p>右侧显示原始抽取值、证据页码和原文定位。</p></div>';
}

async function load() {
  state.uiMode = await api("/api/ui-mode");
  applyUiMode();
  if (isReadOnly()) {
    renderPublicSearchOnlyMode();
    switchView("search");
    return;
  }
  [state.papers, state.testSet, state.uploads, state.jobs, state.ai] = await Promise.all([
    api("/api/papers"),
    apiOptional("/api/test-set", { paper_count: 0, papers: [] }, "全库测试集"),
    apiOptional("/api/uploads", [], "上传记录"),
    apiOptional("/api/processing-jobs", [], "处理队列"),
    apiOptional("/api/ai/status", { configured: false }, "DeepSeek 状态"),
  ]);
  state.mineru = state.ai?.mineru || null;
  await loadCurrentPaper();
  renderPaperOptions();
  renderUploadWorkspace();
}

async function loadCurrentPaper() {
  clearTimeout(state.cloudProgressTimer);
  let visualModeState;
  [state.paper, state.rows, state.visualAssets, state.extraction, state.experimentProfile, state.learning, state.allLearning, state.learningReport, state.allLearningReport, state.deepseekRun, state.cloudRuns, state.cloudCandidates, state.cloudQuality, visualModeState] = await Promise.all([
    api("/api/current-paper"),
    api("/api/six-data"),
    apiOptional("/api/current-paper/visual-assets", [], "当前文章图表证据"),
    apiOptional("/api/current-paper/extraction", null, "当前文章处理状态"),
    apiOptional("/api/current-paper/experiment-profile", null, "实验类型识别"),
    apiOptional("/api/current-paper/learning-samples", null, "当前文章学习样本"),
    apiOptional("/api/learning-samples", null, "全库学习样本"),
    apiOptional("/api/current-paper/learning-report", null, "当前文章学习报告"),
    apiOptional("/api/learning-report", null, "全库学习报告"),
    apiOptional("/api/current-paper/deepseek-run", null, "DeepSeek 运行记录"),
    apiOptional("/api/current-paper/cloud-visual-runs", [], "云端图表运行记录"),
    apiOptional("/api/current-paper/cloud-visual-candidates", [], "云端图表候选"),
    apiOptional("/api/current-paper/cloud-visual-quality", null, "云端图表质量门"),
    apiOptional("/api/visual-processing-mode", { mode: "legacy" }, "图表数据源模式"),
  ]);
  state.visualMode = visualModeState?.mode || "legacy";
  state.audit = null;
  state.reviewVisibleLimit = reviewPageSize;
  state.calibrationActive = false;
  if (state.reviewFilter === "calibration") state.reviewFilter = "all";
  const reviewFilter = document.querySelector("#review-filter");
  if (reviewFilter) reviewFilter.value = state.reviewFilter;
  restoreCalibrationBatch();
  applyReviewPriorities();
  clearDirtyRows();
  state.selected = null;
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderExperimentProfile();
  renderEvidenceAudit();
  renderDeepSeekRun();
  renderReviewObject();
  renderTable();
  renderHistory();
  fillManualDefaults();
  void loadEvidenceAuditForPaper(Number(state.paper.id));
}

async function loadEvidenceAuditForPaper(paperId) {
  try {
    const audit = await api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(paperId)}`);
    if (Number(state.paper?.id) !== Number(paperId)) return;
    state.audit = audit;
    applyReviewPriorities();
    renderEvidenceAudit();
    if (!hasUnsavedEdits()) renderTable();
  } catch (error) {
    if (Number(state.paper?.id) !== Number(paperId)) return;
    const el = document.querySelector("#paper-evidence-audit");
    if (el) {
      el.textContent = `证据定位检查未完成：${error.message}`;
      el.className = "audit-warning";
    }
  }
}

function applyReviewPriorities() {
  const priorities = new Map((state.audit?.review_priority_rows || []).map(item => [Number(item.item_id), item]));
  state.rows.forEach(row => {
    const priority = priorities.get(Number(row.item_id)) || {};
    row.review_priority_level = priority.level || "normal";
    row.review_priority_label = priority.label || "常规核验";
    row.review_priority_score = Number(priority.score || 0);
    row.review_priority_reasons = priority.reasons || [];
  });
}

function paperSearchText(paper) {
  return [
    paper?.title,
    paper?.doi,
    paper?.first_author,
    paper?.corresponding_author,
    paper?.year,
    paper?.material_focus,
  ].filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");
}

function paperAuthorText(paper) {
  return [paper?.first_author, paper?.corresponding_author].filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");
}

function readRecentPaperIds() {
  try {
    const ids = JSON.parse(window.localStorage.getItem(recentPapersStorageKey) || "[]");
    return Array.isArray(ids) ? ids.map(Number).filter(Number.isInteger).slice(0, 8) : [];
  } catch (_error) {
    return [];
  }
}

function rememberRecentPaper(paperId) {
  try {
    const id = Number(paperId);
    const ids = [id, ...readRecentPaperIds().filter(item => item !== id)].slice(0, 8);
    window.localStorage.setItem(recentPapersStorageKey, JSON.stringify(ids));
  } catch (_error) {
    // Recent navigation is optional; article switching remains available.
  }
}

function syncPaperFilterControls() {
  document.querySelector("#paper-picker-query").value = state.paperFilters.query;
  document.querySelector("#paper-author-filter").value = state.paperFilters.author;
  document.querySelector("#paper-status-filter").value = state.paperFilters.status;
}

function applyPaperScopePreset(scope) {
  state.paperFilters = { query: "", author: "", topic: "all", status: "all", scope };
  syncPaperFilterControls();
  renderPaperOptions();
}

function paperMatchesFilters(paper) {
  const filters = state.paperFilters;
  const queryTerms = filters.query.trim().toLocaleLowerCase("zh-CN").split(/\s+/).filter(Boolean);
  if (queryTerms.length && !queryTerms.every(term => paperSearchText(paper).includes(term))) return false;
  const authorTerms = filters.author.trim().toLocaleLowerCase("zh-CN").split(/\s+/).filter(Boolean);
  if (authorTerms.length && !authorTerms.every(term => paperAuthorText(paper).includes(term))) return false;
  const navigationTags = [...(paper.navigation_object_tags || []), ...(paper.navigation_method_tags || [])];
  if (filters.topic !== "all" && !navigationTags.includes(filters.topic)) return false;
  if (filters.scope === "test_set") {
    const testIds = new Set((state.testSet?.papers || []).map(item => Number(item.paper_id)));
    if (!testIds.has(Number(paper.id))) return false;
  } else if (filters.scope === "recent") {
    if (!new Set(readRecentPaperIds()).has(Number(paper.id))) return false;
  }
  const workflow = paper.six_workflow_state || "not_scanned";
  if (filters.status === "scanned" && Number(paper.six_row_count || 0) <= 0) return false;
  if (!["all", "scanned"].includes(filters.status) && workflow !== filters.status) return false;
  return true;
}

function primaryPaperGroup(paper) {
  const methodTags = paper.navigation_method_tags || [];
  const objectTags = paper.navigation_object_tags || [];
  return ["辐照实验", "辐照模拟/计算", "显微/缺陷表征", "力学性能", "氢同位素行为", "热学/热分析", "光谱/能谱", "材料制备"]
    .find(tag => methodTags.includes(tag)) || objectTags[0] || "其他文章";
}

function paperOptionHtml(papers, testOrder, includeGroups = true) {
  const testTotal = testOrder.size;
  const optionFor = paper => {
    const rows = Number(paper.six_row_count || paper.row_count || 0);
    const scan = paper.six_workflow_label || (rows ? `${rows}条数据` : "未扫描");
    const folded = Number(paper.six_semantic_duplicate_count || 0);
    const order = testOrder.get(Number(paper.id));
    const prefix = order ? `【测试集 ${order}/${testTotal}】` : "";
    return `<option value="${esc(paper.id)}">${esc(prefix)}${esc(paperLabel(paper))} · ${esc(scan)}${folded ? ` · 已合并${folded}条重复` : ""}</option>`;
  };
  const testPapers = [...papers].filter(paper => testOrder.has(Number(paper.id))).sort((a, b) => testOrder.get(Number(a.id)) - testOrder.get(Number(b.id)));
  const otherPapers = papers.filter(paper => !testOrder.has(Number(paper.id)));
  if (!includeGroups) return papers.map(optionFor).join("");
  const groupOrder = ["辐照实验", "辐照模拟/计算", "显微/缺陷表征", "力学性能", "氢同位素行为", "热学/热分析", "光谱/能谱", "材料制备", "聚变堆材料", "高熵/中熵合金", "钨与难熔合金", "其他材料", "其他文章"];
  const grouped = new Map();
  otherPapers.forEach(paper => {
    const group = primaryPaperGroup(paper);
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(paper);
  });
  const groups = groupOrder.filter(group => grouped.has(group)).map(group => (
    `<optgroup label="${esc(group)}">${grouped.get(group).sort((a, b) => paperLabel(a).localeCompare(paperLabel(b), "zh-CN")).map(optionFor).join("")}</optgroup>`
  ));
  return [testPapers.length ? `<optgroup label="${testTotal} 篇全库测试集">${testPapers.map(optionFor).join("")}</optgroup>` : "", ...groups].join("");
}

function renderAuthorOptions() {
  const datalist = document.querySelector("#paper-author-options");
  if (!datalist) return;
  const names = new Set();
  state.papers.forEach(paper => {
    if (paper.first_author) names.add(paper.first_author);
    if (paper.corresponding_author) names.add(paper.corresponding_author);
  });
  datalist.innerHTML = [...names].sort((a, b) => a.localeCompare(b, "zh-CN")).map(name => `<option value="${esc(name)}"></option>`).join("");
}

function renderPaperSelectionMeta() {
  const select = document.querySelector("#paper-switch-input");
  const meta = document.querySelector("#paper-selected-meta");
  if (!select || !meta) return;
  const paper = state.papers.find(item => String(item.id) === select.value);
  if (!paper) {
    meta.innerHTML = "<span>当前筛选条件下没有匹配文章。</span>";
    return;
  }
  const authors = [paper.first_author ? `一作：${paper.first_author}` : "", paper.corresponding_author ? `通讯：${paper.corresponding_author}` : ""].filter(Boolean);
  const tags = [...(paper.navigation_object_tags || []), ...(paper.navigation_method_tags || [])];
  meta.innerHTML = [
    authors.length ? `<span>${esc(authors.join(" · "))}</span>` : `<span>作者信息待补充</span>`,
    ...tags.map(tag => `<i>${esc(tag)}</i>`),
  ].join("");
}

function renderPaperOptions() {
  const testOrder = new Map((state.testSet?.papers || []).map(item => [Number(item.paper_id), Number(item.order)]));
  const filteredPapers = state.papers.filter(paperMatchesFilters);
  const switchSelect = document.querySelector("#paper-switch-input");
  if (switchSelect) {
    const previous = switchSelect.value;
    switchSelect.innerHTML = filteredPapers.length
      ? paperOptionHtml(filteredPapers, testOrder)
      : '<option value="">没有匹配文章</option>';
    const preferred = filteredPapers.some(paper => String(paper.id) === previous)
      ? previous
      : filteredPapers.some(paper => String(paper.id) === String(state.paper?.id))
        ? String(state.paper.id)
        : String(filteredPapers[0]?.id || "");
    switchSelect.value = preferred;
    switchSelect.disabled = !filteredPapers.length;
  }
  const manualSelect = document.querySelector("#manual-paper-select");
  if (manualSelect) {
    const previous = manualSelect.value;
    manualSelect.innerHTML = paperOptionHtml(state.papers, testOrder);
    if (previous && state.papers.some(paper => String(paper.id) === previous)) {
      manualSelect.value = previous;
    } else if (state.paper) {
      manualSelect.value = String(state.paper.id);
    }
  }
  const submit = document.querySelector("#paper-switch-submit");
  if (submit) submit.disabled = !filteredPapers.length;
  const summary = document.querySelector("#paper-filter-summary");
  if (summary) summary.textContent = `找到 ${filteredPapers.length} / ${state.papers.length} 篇`;
  document.querySelectorAll("[data-paper-scope]").forEach(button => {
    const active = button.dataset.paperScope === state.paperFilters.scope;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("[data-paper-topic]").forEach(button => {
    const active = button.dataset.paperTopic === state.paperFilters.topic;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  renderAuthorOptions();
  renderPaperSelectionMeta();
  renderPaperStatusSummary();
}

function renderPaperStatusSummary() {
  const el = document.querySelector("#paper-status-summary");
  if (!el) return;
  const total = state.papers.length;
  const scanned = state.papers.filter(p => (Number(p.six_row_count || 0) > 0) || Number(p.completed_ai_run_count || 0) > 0).length;
  const pending = state.papers.filter(p => p.six_workflow_state === "pending_review").length;
  const reviewed = state.papers.filter(p => p.six_workflow_state === "reviewed").length;
  const testSetCount = Number(state.testSet?.paper_count || 0);
  el.textContent = `文章处理总览：共 ${total} 篇；全库测试集 ${testSetCount} 篇；已扫描 ${scanned} 篇；待人工审核 ${pending} 篇；已完成 ${reviewed} 篇。`;
  el.className = pending ? "ready" : reviewed ? "supported" : "";
}

function renderPaper() {
  const paper = state.paper;
  if (!paper) return;
  const paperStatus = state.papers.find(p => String(p.id) === String(paper.id)) || paper;
  const authorYear = [paper.first_author || "作者待补", paper.year || "年份待补"].filter(Boolean).join(" · ");
  setText("paper-key", authorYear);
  setText("paper-title", paper.title);
  setText("paper-doi", paper.doi || "—");
  setText("paper-process-status", paperStatus.six_workflow_label || "未扫描");
  setText("paper-compact-status", `${paperStatus.six_workflow_label || "未扫描"} · ${state.rows.length} 个独立事实`);
  setText("focus-paper-title", paper.title || "当前文章");
  setText("mini-title", paper.title);
  setText("mini-doi", paper.doi || "—");
  setText("nav-count", state.rows.length);
  document.querySelector("#open-paper").href = `/api/papers/${paper.id}/pdf`;
  document.querySelector("#current-export-csv").href = `/api/current-paper/export.csv?paper_id=${encodeURIComponent(paper.id)}`;
  document.querySelector("#current-export-xlsx").href = `/api/current-paper/export.xlsx?paper_id=${encodeURIComponent(paper.id)}`;
  updateReviewBatchLinks();
}

async function refreshReviewFeedback({ includeAudit = false } = {}) {
  const currentPaperId = state.paper?.id;
  const commonRequests = [
    api("/api/papers"),
    api("/api/learning-samples"),
    api("/api/learning-report"),
  ];
  const paperRequests = [];
  if (currentPaperId) {
    paperRequests.push(
      api(`/api/current-paper/learning-samples?paper_id=${encodeURIComponent(currentPaperId)}`),
      api(`/api/current-paper/learning-report?paper_id=${encodeURIComponent(currentPaperId)}`),
      api(`/api/current-paper/extraction?paper_id=${encodeURIComponent(currentPaperId)}`),
    );
    if (includeAudit) {
      paperRequests.push(api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(currentPaperId)}`));
    }
  }
  const [[papers, allLearning, allLearningReport], paperPayload] = await Promise.all([
    Promise.all(commonRequests),
    Promise.all(paperRequests),
  ]);
  state.papers = papers;
  state.allLearning = allLearning;
  state.allLearningReport = allLearningReport;
  if (currentPaperId) {
    state.learning = paperPayload[0];
    state.learningReport = paperPayload[1];
    state.extraction = paperPayload[2];
    if (includeAudit) state.audit = paperPayload[3];
    applyReviewPriorities();
  }
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderEvidenceAudit();
  renderHistory();
}

function setReviewRowBusy(itemId, busy, label = "正在保存…") {
  const row = document.querySelector(`#edit-rows tr[data-item="${Number(itemId)}"]`);
  if (!row) return;
  row.setAttribute("aria-busy", busy ? "true" : "false");
  row.querySelectorAll("button").forEach(button => { button.disabled = busy; });
  const status = row.querySelector(".row-action>small");
  if (busy && status) status.textContent = label;
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
    ? `已整理为 ${status.row_count} 个独立事实${status.completed_ai_run_count ? `；DeepSeek 已完成 ${status.completed_ai_run_count} 次` : ""}`
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

function renderExperimentProfile() {
  const card = document.querySelector("#experiment-profile-card");
  const summary = document.querySelector("#experiment-profile-summary");
  const tags = document.querySelector("#experiment-profile-tags");
  if (!card || !summary || !tags) return;
  const profile = state.experimentProfile;
  if (!profile) {
    summary.textContent = "尚未获得实验类型识别结果。";
    tags.innerHTML = "";
    card.classList.add("warn");
    return;
  }
  const confidence = Math.round((profile.confidence || 0) * 100);
  card.classList.toggle("warn", !profile.is_experimental);
  summary.textContent = profile.is_experimental
    ? `主类型：${profile.primary_label}；置信度 ${confidence}%。系统会按该类型选择抽取重点，再生成六列数据。`
    : `暂未稳定识别为实验论文；建议先人工检查 PDF 文本或扩大识别页数。`;
  const typeTags = (profile.types || []).slice(0, 5).map((item, index) => (
    `<span class="${index === 0 ? "primary" : ""}">${esc(item.label)} · ${esc(item.score)}</span>`
  ));
  tags.innerHTML = typeTags.length
    ? typeTags.join("")
    : `<span>未识别到稳定实验类型</span>`;
}

function renderEvidenceAudit() {
  const audit = state.audit;
  const el = document.querySelector("#paper-evidence-audit");
  if (!el) return;
  if (!audit) {
    el.textContent = "正在后台核验证据定位；校对表已可使用。";
    el.className = "ready";
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
  const findingCount = Number(run.qualitative_finding_count || 0);
  const importedFindings = Number(run.qualitative_imported?.inserted || 0);
  const findingText = findingCount
    ? ` · 定性结论 ${esc(findingCount)} 条（新增 ${esc(importedFindings)} 条）`
    : "";
  const tableCount = state.visualAssets.filter(asset => asset.asset_type === "table").length;
  const figureCount = state.visualAssets.filter(asset => asset.asset_type === "figure").length;
  const visualText = tableCount || figureCount ? ` · 图表证据 ${tableCount} 张表 / ${figureCount} 幅图` : " · 未定位到可截图图表";
  if (run.status === "failed" && state.rows.length) {
    el.innerHTML = `最近一次 DeepSeek 运行在后处理阶段失败，但此前已保存的 ${state.rows.length} 条数据和${visualText.replace(/^ · /, "")}仍可校对。失败原因：${esc(run.error_message || "未记录")}${link}`;
    el.className = "unsupported";
    return;
  }
  el.innerHTML = `最近一次 DeepSeek 运行：${esc(run.status)} · 数值候选 ${esc(run.candidate_count)} 条 · 证据验证通过 ${esc(run.verified_count)} 条${findingText}${visualText} · 重复 ${esc(run.duplicate_count || 0)} 条 · 拒绝/歧义 ${esc(run.rejected_count)} 条 · 候选结果已保存，须在校对页人工确认${link}`;
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
    "读取 PDF 并建立图表截图",
    "按页分块识别数值与定性结论",
    "核对证据页码与原文片段",
    "聚合物理事实并生成可校对结果",
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
  if (rejectReadOnlyAction("调用 DeepSeek 抽取")) return;
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
    const visuals = result.visual_evidence || {};
    finishProgress(`完成：候选 ${result.candidate_count} 条，证据核验通过 ${result.verified_count} 条；图表 ${visuals.table_count || 0} 张表 / ${visuals.figure_count || 0} 幅图。`);
    toast(`DeepSeek 抽取完成：通过 ${result.verified_count} 条；已建立 ${visuals.table_count || 0} 张表和 ${visuals.figure_count || 0} 幅图。`);
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
  if (state.reviewFilter === "calibration") {
    rows = rows.filter(row => state.calibrationReviewIds.has(Number(row.item_id)) && isUnreviewedRow(row));
  } else if (state.reviewFilter === "unreviewed") {
    rows = rows.filter(row => row.origin_type !== "manual" && row.version_no === 0);
  } else if (state.reviewFilter === "priority") {
    rows = rows.filter(row => isUnreviewedRow(row) && ["high", "medium"].includes(row.review_priority_level));
  } else if (state.reviewFilter === "confirmed") {
    rows = rows.filter(row => row.review_action === "confirmation");
  } else if (state.reviewFilter === "corrected") {
    rows = rows.filter(row => row.review_action === "correction");
  } else if (state.reviewFilter === "ambiguous") {
    rows = rows.filter(row => row.review_action === "ambiguous");
  } else if (state.reviewFilter === "rejected") {
    rows = rows.filter(row => row.review_action === "rejected");
  } else if (state.reviewFilter === "manual") {
    rows = rows.filter(row => row.origin_type === "manual");
  }
  return sortReviewRows(q ? rows.filter(row => rowFilterText(row).includes(q)) : rows);
}

function sourcePageNumber(row) {
  const raw = row.original_source_page ?? row.source_page;
  const match = String(raw ?? "").match(/\d+/);
  return match ? Number(match[0]) : Number.POSITIVE_INFINITY;
}

function sourceLocatorText(row) {
  return String(row.original_source_locator || row.source_locator || "").toLowerCase();
}

function sortReviewRows(rows) {
  const withIndex = rows.map((row, index) => ({ row, index }));
  if (state.reviewSort === "source_asc") {
    withIndex.sort((a, b) => (
      sourcePageNumber(a.row) - sourcePageNumber(b.row)
      || sourceLocatorText(a.row).localeCompare(sourceLocatorText(b.row))
      || a.index - b.index
    ));
  } else if (state.reviewSort === "review_priority") {
    withIndex.sort((a, b) => (
      Number(b.row.review_priority_score || 0) - Number(a.row.review_priority_score || 0)
      || Number(!isUnreviewedRow(a.row)) - Number(!isUnreviewedRow(b.row))
      || sourcePageNumber(a.row) - sourcePageNumber(b.row)
      || a.index - b.index
    ));
  } else if (state.reviewSort === "source_desc") {
    withIndex.sort((a, b) => (
      sourcePageNumber(b.row) - sourcePageNumber(a.row)
      || sourceLocatorText(b.row).localeCompare(sourceLocatorText(a.row))
      || a.index - b.index
    ));
  } else if (state.reviewSort === "unreviewed_source") {
    withIndex.sort((a, b) => (
      Number(!isUnreviewedRow(a.row)) - Number(!isUnreviewedRow(b.row))
      || sourcePageNumber(a.row) - sourcePageNumber(b.row)
      || sourceLocatorText(a.row).localeCompare(sourceLocatorText(b.row))
      || a.index - b.index
    ));
  }
  return withIndex.map(item => item.row);
}

function rowFilterText(row) {
  const parts = [
    row.item_id,
    `#${row.item_id}`,
    `item_id=${row.item_id}`,
    row.stable_key,
    row.source_page,
    row.original_source_page,
    row.source_locator,
    row.original_source_locator,
    ...fields.map(field => row[field]),
  ];
  return parts.map(value => String(value ?? "").toLowerCase()).join(" ");
}

function reviewProgress() {
  const total = state.rows.length;
  const manual = state.rows.filter(row => row.origin_type === "manual").length;
  const confirmed = state.rows.filter(row => row.review_action === "confirmation").length;
  const corrected = state.rows.filter(row => row.review_action === "correction").length;
  const rejected = state.rows.filter(row => row.review_action === "rejected").length;
  const ambiguous = state.rows.filter(row => row.review_action === "ambiguous").length;
  const reviewed = manual + confirmed + corrected + rejected + ambiguous;
  return { total, reviewed, unreviewed: Math.max(total - reviewed, 0), confirmed, corrected, rejected, ambiguous, manual };
}

function isUnreviewedRow(row) {
  return row.origin_type !== "manual" && row.review_action === "automatic";
}

function updateReviewBatchLinks() {
  const paperId = state.paper?.id;
  if (!paperId) return;
  const unreviewed = state.rows.filter(isUnreviewedRow).length;
  const encoded = encodeURIComponent(paperId);
  const batch = document.querySelector("#current-review-batch");
  const calibration = document.querySelector("#current-review-calibration");
  const all = document.querySelector("#current-review-all");
  if (batch) batch.href = `/api/current-paper/review-batch.md?paper_id=${encoded}&limit=20`;
  if (calibration) calibration.href = `/api/current-paper/review-batch.md?paper_id=${encoded}&limit=20&strategy=calibration`;
  if (all) {
    const limit = Math.max(unreviewed, 1);
    all.href = `/api/current-paper/review-batch.md?paper_id=${encoded}&limit=${encodeURIComponent(limit)}`;
    all.textContent = unreviewed ? `下载全部待审核（${unreviewed}）` : "下载全部待审核";
    all.classList.toggle("disabled", unreviewed === 0);
    all.setAttribute("aria-disabled", unreviewed === 0 ? "true" : "false");
  }
}

function renderCalibrationReviewState() {
  const button = document.querySelector("#review-calibration-start");
  if (!button) return;
  const remaining = state.rows.filter(row => state.calibrationReviewIds.has(Number(row.item_id)) && isUnreviewedRow(row)).length;
  if (!state.calibrationActive && state.calibrationReviewIds.size && !remaining) {
    clearSavedCalibrationBatch(state.paper?.id);
    state.calibrationReviewIds = new Set();
    state.calibrationBatchTotal = 0;
  }
  button.setAttribute("aria-pressed", state.calibrationActive ? "true" : "false");
  button.classList.toggle("active", state.calibrationActive);
  button.textContent = state.calibrationActive
    ? remaining
      ? `暂离本轮校准（剩余 ${remaining}/${state.calibrationBatchTotal}）`
      : "完成并退出本轮校准"
    : remaining
      ? `继续本轮校准（剩余 ${remaining}/${state.calibrationBatchTotal}）`
      : "开始分层校准（20 条）";
}

async function toggleCalibrationReview() {
  if (rejectReadOnlyAction("启动分层校准")) return;
  if (hasUnsavedEdits() && !confirmDiscardUnsaved("切换分层校准模式")) return;
  if (state.calibrationActive) {
    const remaining = state.rows.filter(row => state.calibrationReviewIds.has(Number(row.item_id)) && isUnreviewedRow(row)).length;
    state.calibrationActive = false;
    state.reviewFilter = "all";
    document.querySelector("#review-filter").value = "all";
    state.selected = null;
    if (!remaining) {
      clearSavedCalibrationBatch(state.paper?.id);
      state.calibrationReviewIds = new Set();
      state.calibrationBatchTotal = 0;
    }
    renderTable();
    toast(remaining
      ? `已暂离本轮校准，剩余 ${remaining} 条已保存在本机浏览器，可稍后继续。`
      : "本轮分层校准已完成，可以开始下一批。未改变自动抽取原始版本。");
    return;
  }
  const savedRemaining = state.rows.filter(row => state.calibrationReviewIds.has(Number(row.item_id)) && isUnreviewedRow(row));
  if (savedRemaining.length) {
    state.calibrationActive = true;
    state.reviewFilter = "calibration";
    document.querySelector("#review-filter").value = "calibration";
    state.selected = Number(savedRemaining[0].item_id);
    renderTable();
    window.requestAnimationFrame(() => {
      document.querySelector(`#edit-rows tr[data-item="${state.selected}"]`)?.scrollIntoView({ block: "center" });
    });
    toast(`已恢复本轮分层校准：剩余 ${savedRemaining.length}/${state.calibrationBatchTotal} 条。`);
    return;
  }
  const button = document.querySelector("#review-calibration-start");
  button.disabled = true;
  try {
    const params = new URLSearchParams({ paper_id: state.paper.id, limit: "20", strategy: "calibration" });
    const payload = await api(`/api/current-paper/review-batch?${params}`);
    const ids = (payload.selected_item_ids || []).map(Number);
    if (!ids.length) {
      toast("当前文章已经没有待审核数据。", false);
      return;
    }
    state.calibrationReviewIds = new Set(ids);
    state.calibrationBatchTotal = ids.length;
    state.calibrationActive = true;
    saveCalibrationBatch(state.paper.id, ids);
    state.reviewFilter = "calibration";
    document.querySelector("#review-filter").value = "calibration";
    state.selected = ids[0];
    renderTable();
    window.requestAnimationFrame(() => {
      document.querySelector(`#edit-rows tr[data-item="${ids[0]}"]`)?.scrollIntoView({ block: "center" });
    });
    toast(`已进入分层校准模式：从 ${payload.remaining_unreviewed} 个待审核事实中选出 ${ids.length} 个代表性样本。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    renderCalibrationReviewState();
  }
}

function autoSizeReviewCell(input) {
  input.style.height = "auto";
  const target = Math.min(Math.max(input.scrollHeight + 2, 46), 132);
  input.style.height = `${target}px`;
  input.style.overflowY = input.scrollHeight > target ? "auto" : "hidden";
}

function renderTable() {
  const allRows = filteredRows();
  const rows = allRows.slice(0, state.reviewVisibleLimit);
  const progress = reviewProgress();
  renderReviewProgressCard(progress);
  renderCalibrationReviewState();
  updateReviewBatchLinks();
  const filterNote = state.reviewFilter === "all" ? "" : ` · 当前筛出 ${allRows.length} 条`;
  const visibleNote = rows.length < allRows.length ? ` · 当前显示 ${rows.length}/${allRows.length} 条` : "";
  const sortNote = state.reviewSort === "original" ? "" : ` · ${document.querySelector("#review-sort")?.selectedOptions?.[0]?.textContent || "已排序"}`;
  const dirtyNote = hasUnsavedEdits() ? `，${state.dirtyRows.size} 行未确认` : "";
  const breakdown = `确认 ${progress.confirmed}、修正 ${progress.corrected}、歧义 ${progress.ambiguous}、不采用 ${progress.rejected}、人工 ${progress.manual}`;
  setText("row-count", `${progress.reviewed}/${progress.total} 个事实已审核（${breakdown}），${progress.unreviewed} 个待审核${dirtyNote}${filterNote}${visibleNote}${sortNote}`);
  const loadMore = document.querySelector("#review-load-more");
  if (loadMore) {
    loadMore.hidden = rows.length >= allRows.length;
    loadMore.textContent = rows.length < allRows.length ? `显示更多（剩余 ${allRows.length - rows.length}）` : "显示更多";
  }
  const body = document.querySelector("#edit-rows");
  if (!rows.length) {
    const message = state.reviewFilter === "calibration"
      ? "本轮分层校准已完成，或所选样本已全部处理。退出校准模式后可继续审核其余数据。"
      : "当前筛选条件下没有数据。你可以切回“全部”或“只看未审核”。";
    body.innerHTML = `<tr><td colspan="7"><div class="empty-table">${message}</div></td></tr>`;
    state.selected = null;
    renderOriginalPlaceholder();
    return;
  }
  if (!state.selected || !rows.some(row => Number(row.item_id) === Number(state.selected))) {
    state.selected = Number(rows[0].item_id);
  }
  body.innerHTML = rows.map(row => {
    const priorityClass = isUnreviewedRow(row) && row.review_priority_level !== "normal" ? `priority-${row.review_priority_level}` : "";
    const cls = [state.selected === row.item_id ? "selected" : "", state.dirtyRows.has(Number(row.item_id)) ? "dirty" : "", row.review_action === "confirmation" ? "confirmed" : "", row.review_action === "correction" ? "revised" : "", row.review_action === "rejected" ? "rejected" : "", row.review_action === "ambiguous" ? "ambiguous" : "", row.origin_type === "manual" ? "manual" : "", priorityClass].filter(Boolean).join(" ");
    const readonly = isReadOnly() ? " readonly" : "";
    const cells = fields.map(field => `<td><textarea class="cell ${field === "context_explanation" ? "context" : ""}" data-field="${field}" aria-label="${fieldLabels[field]}"${readonly}>${esc(row[field])}</textarea></td>`).join("");
    const badge = row.origin_type === "manual" ? "人工" : row.review_action === "confirmation" ? `已确认 v${row.version_no}` : row.review_action === "correction" ? `已修正 v${row.version_no}` : row.review_action === "rejected" ? `不采用 v${row.version_no}` : row.review_action === "ambiguous" ? `存在歧义 v${row.version_no}` : "未审核";
    const sourceDisabled = row.origin_type === "manual" ? " disabled" : "";
    const reviewButtons = isReadOnly()
      ? ""
      : `<button class="confirm" data-confirm="${row.item_id}">确认当前内容</button><button class="confirm-next" data-confirm-next="${row.item_id}">确认并下一条</button>`;
    const priorityBadge = isUnreviewedRow(row) && row.review_priority_level !== "normal"
      ? `<span class="review-priority ${esc(row.review_priority_level)}" title="${esc((row.review_priority_reasons || []).join("；"))}">${esc(row.review_priority_label)}</span>`
      : "";
    const decisionButtons = isReadOnly() || row.origin_type === "manual"
      ? ""
      : isUnreviewedRow(row)
        ? `<details class="row-review-more"><summary>其他决定</summary><button type="button" data-decision="ambiguous" data-item-id="${row.item_id}">存在歧义</button><button type="button" data-decision="rejected" data-item-id="${row.item_id}">不采用</button></details>`
        : ["confirmation", "correction", "rejected", "ambiguous"].includes(row.review_action)
          ? `<button type="button" class="reopen-action" data-reopen="${row.item_id}">恢复待审核</button>`
          : "";
    const clusterNote = Number(row.fact_cluster_size || 1) > 1
      ? `<span class="review-cluster-note">${row.fact_cluster_size} 条重复记录已合并</span>`
      : "";
    const evidenceMenu = Number(row.evidence_count || 0) > 1
      ? `<details class="row-evidence-menu"><summary>${row.evidence_count} 处证据</summary>${(row.evidence_occurrences || []).map((source, index) => `<button type="button" data-source-member="${source.item_id}">证据 ${index + 1} · 第${source.page || "?"}页</button>`).join("")}</details>`
      : `<button class="source-action" data-source-row="${row.item_id}"${sourceDisabled}>原文证据</button>`;
    return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action">${clusterNote}${priorityBadge}${reviewButtons}${decisionButtons}<button data-original="${row.item_id}">查看原始</button>${evidenceMenu}<small>#${row.item_id} · ${badge}</small></div></td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-item]").forEach(tr => tr.addEventListener("click", event => {
    if (event.target.closest("button[data-confirm],button[data-confirm-next],button[data-source-row]")) return;
    selectRow(Number(tr.dataset.item));
  }));
  body.querySelectorAll("[data-original]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    selectRow(Number(btn.dataset.original));
  }));
  body.querySelectorAll("[data-source-member]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    openSourceViewer(Number(btn.dataset.sourceMember));
  }));
  body.querySelectorAll("[data-confirm]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    confirmRow(Number(btn.dataset.confirm));
  }));
  body.querySelectorAll("[data-confirm-next]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    confirmRow(Number(btn.dataset.confirmNext), { goNext: true });
  }));
  body.querySelectorAll("[data-source-row]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    selectRow(Number(btn.dataset.sourceRow));
    openSourceViewer(Number(btn.dataset.sourceRow));
  }));
  body.querySelectorAll("[data-decision]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    openReviewDecision(Number(btn.dataset.itemId), btn.dataset.decision);
  }));
  body.querySelectorAll("[data-reopen]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    reopenReviewDecision(Number(btn.dataset.reopen));
  }));
  body.querySelectorAll("[data-field]").forEach(input => {
    autoSizeReviewCell(input);
    input.addEventListener("input", event => {
      autoSizeReviewCell(event.target);
      if (isReadOnly()) return;
      const tr = event.target.closest("tr[data-item]");
      const id = Number(tr?.dataset.item);
      const row = state.rows.find(item => item.item_id === id);
      if (!row) return;
      const fieldsDirty = rowFieldsDirty(id);
      if (fieldsDirty) state.fieldDirtyRows.add(id);
      else state.fieldDirtyRows.delete(id);
      markRowDirty(id, fieldsDirty || Boolean(String(state.reviewNotes.get(id) || "").trim()));
    });
  });
  renderOriginal(rows.find(row => Number(row.item_id) === Number(state.selected)) || null);
}

function visualListText(values) {
  return (values || []).join("、");
}

function visualVariablesText(values) {
  return Object.entries(values || {}).map(([key, value]) => `${key}：${value}`).join("\n");
}

function parseVisualList(value) {
  return [...new Set(String(value || "").split(/[、,，;；\n]+/).map(item => item.trim()).filter(Boolean))];
}

function parseVisualVariables(value) {
  const result = {};
  String(value || "").split(/[\n;；]+/).forEach(line => {
    const match = line.trim().match(/^([^:：=]+)[:：=](.+)$/);
    if (match) result[match[1].trim()] = match[2].trim();
  });
  return result;
}

function visualReviewLabel(asset) {
  return {
    automatic: "待审核",
    confirmation: `已确认 v${asset.version_no}`,
    correction: `已修正 v${asset.version_no}`,
    ambiguous: `存在歧义 v${asset.version_no}`,
    rejected: `不采用 v${asset.version_no}`,
  }[asset.review_action] || "待审核";
}

function setReviewObject(mode) {
  if (!["data", "table", "figure"].includes(mode)) return;
  if (mode !== "data" && hasUnsavedEdits() && !confirmDiscardUnsaved("切换图表校对")) return;
  state.reviewObject = mode;
  renderReviewObject();
}

function renderReviewObject() {
  const tables = state.visualAssets.filter(asset => asset.asset_type === "table");
  const figures = state.visualAssets.filter(asset => asset.asset_type === "figure");
  setText("review-data-count", `${state.rows.length} 条`);
  setText("review-table-count", `${tables.length} 张`);
  setText("review-figure-count", `${figures.length} 幅`);
  document.querySelectorAll("[data-review-object]").forEach(button => {
    const active = button.dataset.reviewObject === state.reviewObject;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  const dataMode = state.reviewObject === "data";
  document.querySelector("#data-review-workspace").hidden = !dataMode;
  document.querySelector("#visual-review-workspace").hidden = dataMode;
  const controls = document.querySelector("#view-review .view-actions");
  if (controls) controls.hidden = !dataMode;
  if (!dataMode) renderVisualReview();
}

function visualReviewCard(asset) {
  const attention = ["ambiguous", "rejected"].includes(asset.review_action);
  const reviewed = ["confirmation", "correction"].includes(asset.review_action);
  const statusClass = attention ? "attention" : reviewed ? "reviewed" : "pending";
  const typeName = asset.asset_type === "table" ? "表格" : "图片";
  const readonly = isReadOnly() ? " readonly" : "";
  const actions = isReadOnly() ? "" : `<div class="visual-review-actions">
    <button type="button" class="visual-confirm" data-visual-decision="confirmation">确认当前信息</button>
    <button type="button" data-visual-decision="ambiguous">存在歧义</button>
    <button type="button" data-visual-decision="rejected">不采用</button>
    ${asset.review_action !== "automatic" ? '<button type="button" data-visual-decision="automatic">恢复待审核</button>' : ""}
  </div>`;
  const cloud = state.cloudCandidates.find(candidate => Number(candidate.asset_id) === Number(asset.id) && candidate.asset_type === asset.asset_type);
  const cloudState = cloud ? ({ passed: "已通过", rejected: "已拒绝", ambiguous: "有歧义", pending: "云端候选" }[cloud.quality_status] || "云端候选") : "尚无候选";
  const cloudImage = cloud?.image_url
    ? `<button type="button" class="cloud-candidate-image" data-open-cloud-image="${cloud.id}"><img src="${esc(cloud.image_url)}" alt="${esc(cloud.label)}云端候选" loading="lazy"></button>`
    : `<div class="cloud-candidate-placeholder"><span>云端结构已返回</span><small>未提供独立图像，稳定截图继续保留</small></div>`;
  const cloudActions = !cloud || isReadOnly() ? "" : `<div class="cloud-candidate-actions" data-cloud-source="${cloud.id}">
    <button type="button" data-cloud-decision="keep_stable">保留稳定版</button>
    <button type="button" class="cloud-adopt" data-cloud-decision="adopt_enhancement"${cloud.asset_id ? "" : " disabled"}>采用云端增强</button>
    <button type="button" data-cloud-decision="adopt_interpretation"${cloud.asset_id ? "" : " disabled"}>仅采用云端解释</button>
    <button type="button" class="cloud-error" data-cloud-decision="mark_error">标记云端错误</button>
  </div>`;
  const cloudPanel = cloud ? `<section class="cloud-candidate-panel ${esc(cloud.quality_status)}">
    <header><span>云端候选 · ${esc(cloud.label)} · PDF第 ${esc(cloud.page_start)} 页</span><em>${esc(cloudState)}</em></header>
    ${cloudImage}
    <div class="cloud-candidate-copy">
      <strong>${esc(cloud.display_name || cloud.caption || cloud.label)}</strong>
      <p>${esc(cloud.context_explanation || cloud.source_context || "等待 DeepSeek 语义解释。")}</p>
      <small>${cloud.asset_type === "table" ? `结构化单元格 ${esc(cloud.cell_count || 0)} 个 · 表头 ${esc(cloud.header_row_count || 0)} 行` : `趋势 ${esc((cloud.trends || []).length)} 条 · 禁止曲线点`}</small>
    </div>
    ${cloudActions}
  </section>` : `<section class="cloud-candidate-panel empty"><header><span>云端候选</span><em>未运行</em></header><div class="cloud-candidate-placeholder"><span>稳定版正常可用</span><small>生成候选后将在此并排比较，不会覆盖左侧截图</small></div></section>`;
  return `<article class="visual-review-card ${statusClass}" data-visual-review="${asset.id}">
    <div class="visual-review-proof">
      <div class="visual-review-proof-head"><span>稳定版 · ${esc(asset.label)} · PDF 第 ${esc(asset.page_start)} 页</span><em>${esc(visualReviewLabel(asset))}</em></div>
      <div class="visual-source-compare"><div class="stable-visual-source"><button type="button" class="visual-review-image" data-open-review-visual="${asset.id}" aria-label="放大查看${esc(asset.label)}"><img src="${esc(asset.stable_image_url || asset.image_url)}" alt="${esc(asset.label)}稳定版原文截图" loading="lazy"></button></div>${cloudPanel}</div>
      <div class="visual-proof-actions"><button type="button" data-open-review-visual="${asset.id}">放大查看</button><a href="${esc(asset.pdf_url)}" target="_blank" rel="noopener">打开原文 PDF</a></div>
      <p><strong>原始图注</strong>${esc(asset.original_caption || asset.caption)}</p>
    </div>
    <form class="visual-review-form">
      <header><div><small>${typeName.toUpperCase()} METADATA</small><h3>${esc(asset.label)} 的检索信息</h3></div><span>截图与原图注保持不变</span></header>
      <label class="span-2">简短中文名称<input name="display_name" maxlength="60"${readonly} value="${esc(asset.display_name || asset.label)}" placeholder="例如：辐照前后纳米硬度对比"></label>
      <label>展示的物理量<textarea name="physical_quantities"${readonly} placeholder="用顿号或换行分隔">${esc(visualListText(asset.physical_quantities))}</textarea></label>
      <label>变量或表头<textarea name="variables"${readonly} placeholder="例如 x：Dose；y：Hardness">${esc(visualVariablesText(asset.variables))}</textarea></label>
      <label>材料或样品<textarea name="materials"${readonly}>${esc(visualListText(asset.materials))}</textarea></label>
      <label>测试/分析方法<textarea name="methods_text"${readonly}>${esc(asset.methods_text)}</textarea></label>
      <label class="span-2">实验条件<textarea name="conditions_text"${readonly}>${esc(asset.conditions_text)}</textarea></label>
      <label class="span-2">数据在文中的解释（搜索核心）<textarea name="context_explanation"${readonly}>${esc(asset.context_explanation)}</textarea></label>
      <label class="span-2">检索标签<textarea name="tags"${readonly}>${esc(visualListText(asset.tags))}</textarea></label>
      ${asset.review_note ? `<p class="visual-review-note"><strong>核验说明</strong>${esc(asset.review_note)}</p>` : ""}
      ${actions}
    </form>
  </article>`;
}

function renderVisualReview() {
  const type = state.reviewObject;
  const assets = state.visualAssets.filter(asset => asset.asset_type === type);
  const unmatchedCloud = state.cloudCandidates.filter(candidate => candidate.asset_type === type && !candidate.asset_id);
  const typeName = type === "table" ? "原始表格" : "论文图片";
  setText("visual-review-kicker", type === "table" ? "TABLE EVIDENCE REVIEW" : "FIGURE EVIDENCE REVIEW");
  setText("visual-review-title", `${typeName}校对`);
  const reviewed = assets.filter(asset => asset.review_action !== "automatic").length;
  setText("visual-review-summary", `${reviewed}/${assets.length} 已审核。核对截图、图注与检索标签；图片曲线不会被自动转换为精确数值。`);
  renderCloudVisualStatus();
  const grid = document.querySelector("#visual-review-grid");
  if (!assets.length && !unmatchedCloud.length) {
    grid.innerHTML = `<div class="blank visual-review-empty"><span>▧</span><h3>当前文章尚未建立${typeName}</h3><p>运行“自动提取/核验”后，系统会先从本地 PDF 建立高分辨率图表截图；识别不到的扫描件将保留为待处理任务。</p></div>`;
    return;
  }
  const unmatchedHtml = unmatchedCloud.map(candidate => `<article class="visual-review-card cloud-only attention">
    <div class="visual-review-proof">
      <div class="visual-review-proof-head"><span>新增云端候选 · ${esc(candidate.label)} · PDF第 ${esc(candidate.page_start)} 页</span><em>尚未匹配稳定图表</em></div>
      ${candidate.image_url ? `<button type="button" class="visual-review-image" data-open-cloud-image="${candidate.id}"><img src="${esc(candidate.image_url)}" alt="新增云端候选" loading="lazy"></button>` : '<div class="cloud-candidate-placeholder"><span>没有独立图像</span></div>'}
      <p><strong>云端图注</strong>${esc(candidate.caption || "无")}</p>
    </div>
    <div class="visual-review-form"><header><div><small>UNMATCHED CLOUD CANDIDATE</small><h3>${esc(candidate.display_name || candidate.label)}</h3></div><span>不能直接进入稳定库</span></header>
      <p class="visual-review-note"><strong>需要人工判断</strong>该候选没有与旧图表建立可靠对应关系。第一版只允许保留为影子结果或标记错误，不会自动新建公开图表。</p>
      <div class="cloud-candidate-actions" data-cloud-source="${candidate.id}"><button type="button" data-cloud-decision="keep_stable">保留为影子候选</button><button type="button" class="cloud-error" data-cloud-decision="mark_error">标记云端错误</button></div>
    </div>
  </article>`).join("");
  grid.innerHTML = assets.map(visualReviewCard).join("") + unmatchedHtml;
  grid.querySelectorAll("[data-open-review-visual]").forEach(button => button.addEventListener("click", () => openVisualAsset(Number(button.dataset.openReviewVisual))));
  grid.querySelectorAll("[data-visual-decision]").forEach(button => button.addEventListener("click", () => {
    const card = button.closest("[data-visual-review]");
    saveVisualReview(Number(card.dataset.visualReview), button.dataset.visualDecision, card.querySelector("form"));
  }));
  grid.querySelectorAll("[data-cloud-decision]").forEach(button => button.addEventListener("click", () => {
    const panel = button.closest("[data-cloud-source]");
    reviewCloudVisualCandidate(Number(panel.dataset.cloudSource), button.dataset.cloudDecision);
  }));
  grid.querySelectorAll("[data-open-cloud-image]").forEach(button => button.addEventListener("click", () => {
    window.open(`/api/cloud-visual-sources/${Number(button.dataset.openCloudImage)}/image`, "_blank", "noopener");
  }));
}

function renderCloudVisualStatus() {
  const container = document.querySelector("#cloud-visual-status");
  const button = document.querySelector("#run-cloud-visual");
  const mode = document.querySelector("#visual-processing-mode");
  if (!container || !button || !mode) return;
  mode.value = state.visualMode || "legacy";
  const latest = state.cloudRuns?.[0];
  const configured = Boolean(state.mineru?.configured);
  button.disabled = !configured || !state.paper?.pdf_path || Boolean(latest && !latest.terminal);
  button.title = configured
    ? "上传整篇 PDF 到 MinerU，结果仅写入影子候选层"
    : "请先将 MinerU Token 存入 macOS 钥匙串 auto-research-mineru";
  container.hidden = !latest && !state.cloudCandidates.length;
  if (!latest) {
    setText("cloud-visual-stage", configured ? "尚未生成云端候选" : "MinerU 待配置");
    setText("cloud-visual-message", configured ? "稳定版图表不受影响。" : "稳定版图表仍可正常校对与检索。");
    document.querySelector("#cloud-visual-progress-bar").style.width = "0%";
    setText("cloud-visual-quality", "");
    return;
  }
  const stageLabels = { queued: "准备任务", uploading: "上传 PDF", pending: "云端排队", running: "解析页面", downloading: "下载结果", analyzing: "DeepSeek 语义分析", completed: "候选已生成", failed: "云端任务失败" };
  setText("cloud-visual-stage", stageLabels[latest.status] || latest.progress_stage || "处理中");
  const pageProgress = latest.progress_total ? ` · ${latest.progress_current}/${latest.progress_total} 页` : "";
  const failure = latest.error_message ? ` · ${latest.error_message}` : "";
  setText("cloud-visual-message", `${latest.message || ""}${pageProgress}${failure}`);
  document.querySelector("#cloud-visual-progress-bar").style.width = `${Math.max(0, Math.min(100, latest.progress_percent || 0))}%`;
  const quality = state.cloudQuality;
  setText("cloud-visual-quality", quality
    ? `候选 ${quality.candidate_count} 个 · 已匹配稳定图表 ${quality.matched_count} 个 · 已通过 ${quality.passed_count} 个 · 曲线点违规 ${quality.curve_point_violations} 个`
    : "质量门尚未计算");
  if (!latest.terminal) scheduleCloudVisualPoll(latest.id);
}

function scheduleCloudVisualPoll(runId) {
  clearTimeout(state.cloudProgressTimer);
  state.cloudProgressTimer = setTimeout(async () => {
    try {
      const run = await api(`/api/cloud-visual-runs/${runId}`);
      state.cloudRuns = [run, ...state.cloudRuns.filter(item => Number(item.id) !== Number(run.id))];
      renderCloudVisualStatus();
      if (run.terminal) {
        [state.cloudCandidates, state.cloudQuality, state.visualAssets] = await Promise.all([
          api("/api/current-paper/cloud-visual-candidates"),
          api("/api/current-paper/cloud-visual-quality"),
          api("/api/current-paper/visual-assets"),
        ]);
        renderReviewObject();
        toast(run.status === "completed" ? "云端图表候选已生成，请逐图表比较。" : "云端增强未完成；稳定版图表仍可正常使用。", run.status !== "completed");
      }
    } catch (error) {
      toast(`云端进度读取失败：${error.message}`, true);
    }
  }, 2500);
}

async function runCloudVisual() {
  if (rejectReadOnlyAction("生成云端图表候选")) return;
  if (!state.mineru?.configured) {
    toast("MinerU 尚未配置；稳定版功能不受影响。", true);
    return;
  }
  const button = document.querySelector("#run-cloud-visual");
  button.disabled = true;
  try {
    const run = await api("/api/current-paper/cloud-visual-run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, mode: "shadow" }),
    });
    state.cloudRuns = [run, ...state.cloudRuns.filter(item => Number(item.id) !== Number(run.id))];
    document.querySelector("#cloud-visual-status").hidden = false;
    renderCloudVisualStatus();
    toast("云端影子任务已开始；当前稳定截图不会被修改。");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

async function changeVisualProcessingMode(mode) {
  if (rejectReadOnlyAction("切换图表数据源")) return;
  try {
    const result = await api("/api/visual-processing-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    state.visualMode = result.mode;
    state.visualAssets = await api("/api/current-paper/visual-assets");
    renderReviewObject();
    toast(result.mode === "hybrid" ? "仅已逐项通过的云端候选会生效。" : result.mode === "shadow" ? "已进入影子对比模式，公开结果不变。" : "已切回稳定版图表数据源。");
  } catch (error) {
    document.querySelector("#visual-processing-mode").value = state.visualMode;
    toast(error.message, true);
  }
}

async function reviewCloudVisualCandidate(sourceId, decision) {
  if (rejectReadOnlyAction("核验云端图表候选")) return;
  let note = "";
  if (decision === "mark_error") {
    note = window.prompt("请简要说明云端候选的错误：", "") || "";
    if (!note.trim()) return;
  }
  try {
    const candidate = await api(`/api/cloud-visual-sources/${sourceId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note }),
    });
    state.cloudCandidates = state.cloudCandidates.map(item => Number(item.id) === sourceId ? candidate : item);
    state.cloudQuality = await api("/api/current-paper/cloud-visual-quality");
    state.visualAssets = await api("/api/current-paper/visual-assets");
    renderReviewObject();
    const labels = { keep_stable: "已保留稳定版", adopt_enhancement: "已采用云端增强", adopt_interpretation: "已采用云端解释并保留稳定截图", mark_error: "已标记云端结果错误" };
    toast(labels[decision] || "云端候选处理结果已保存");
  } catch (error) {
    toast(error.message, true);
  }
}

function visualFieldsFromForm(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  return {
    display_name: String(values.display_name || "").trim(),
    physical_quantities: parseVisualList(values.physical_quantities),
    variables: parseVisualVariables(values.variables),
    materials: parseVisualList(values.materials),
    conditions_text: String(values.conditions_text || "").trim(),
    methods_text: String(values.methods_text || "").trim(),
    context_explanation: String(values.context_explanation || "").trim(),
    tags: parseVisualList(values.tags),
  };
}

async function saveVisualReview(assetId, decision, form) {
  if (rejectReadOnlyAction("校对图表")) return;
  let note = "";
  if (["ambiguous", "rejected"].includes(decision)) {
    note = window.prompt(decision === "ambiguous" ? "请说明无法确定的字段或原因：" : "请说明不采用该图表的原因：", "") || "";
    if (!note.trim()) return;
  }
  const button = form.querySelector(`[data-visual-decision="${decision}"]`);
  if (button) button.disabled = true;
  try {
    const result = await api(`/api/visual-assets/${assetId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: visualFieldsFromForm(form), decision, note, reviewer: "本地研究者" }),
    });
    state.visualAssets = state.visualAssets.map(asset => Number(asset.id) === assetId ? result : asset);
    renderReviewObject();
    toast(decision === "automatic" ? "该图表已恢复为待审核。" : `图表核验已保存：${visualReviewLabel(result)}。`);
  } catch (error) {
    toast(error.message, true);
    if (button) button.disabled = false;
  }
}

function selectRow(id) {
  state.selected = id;
  const row = state.rows.find(item => item.item_id === id);
  renderOriginal(row);
  document.querySelectorAll("#edit-rows tr[data-item]").forEach(tr => tr.classList.toggle("selected", Number(tr.dataset.item) === id));
}

function selectNextUnreviewed() {
  let candidates = filteredRows().filter(isUnreviewedRow);
  if (!candidates.length && state.reviewFilter === "calibration") {
    toast("本轮分层校准已完成。可以退出校准模式，或等待这些反馈进入下一轮抽取优化。");
    renderCalibrationReviewState();
    return;
  }
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
  const visibleIndex = filteredRows().findIndex(row => Number(row.item_id) === Number(next.item_id));
  if (visibleIndex >= state.reviewVisibleLimit) {
    state.reviewVisibleLimit = Math.ceil((visibleIndex + 1) / reviewPageSize) * reviewPageSize;
    state.selected = Number(next.item_id);
    renderTable();
  } else {
    selectRow(Number(next.item_id));
  }
  document.querySelector(`#edit-rows tr[data-item="${next.item_id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function confirmSelectedRow(options = {}) {
  if (rejectReadOnlyAction("确认或修正数据")) return;
  if (!state.selected) {
    toast("请先选择一条数据。", true);
    return;
  }
  confirmRow(Number(state.selected), options);
}

function openSelectedSource() {
  if (!state.selected) {
    toast("请先选择一条数据。", true);
    return;
  }
  const row = state.rows.find(item => Number(item.item_id) === Number(state.selected));
  if (!row || row.origin_type === "manual") {
    toast("人工补录数据没有自动抽取的原文定位。", true);
    return;
  }
  openSourceViewer(Number(state.selected));
}

function handleReviewKeyboard(event) {
  if (document.body.dataset.view !== "review") return;
  if (event.key === "Escape" && state.focusReview) {
    event.preventDefault();
    setFocusReview(false);
    return;
  }
  const key = event.key.toLowerCase();
  const commandOrCtrl = event.metaKey || event.ctrlKey;
  if (event.key === "Enter" && commandOrCtrl) {
    event.preventDefault();
    confirmSelectedRow({ goNext: event.shiftKey });
  } else if (key === "n" && event.altKey && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    selectNextUnreviewed();
  } else if (key === "s" && event.altKey && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    openSelectedSource();
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
  const reviewNote = state.reviewNotes.get(Number(row.item_id)) || "";
  const relatedVisuals = (row.visual_assets || []).map(asset => `<button type="button" data-review-related-visual="${asset.id}" data-review-related-type="${esc(asset.asset_type)}"><img src="${esc(asset.image_url)}" alt="${esc(asset.label)}截图"><span>${asset.asset_type === "table" ? "原始表格" : "相关图片"}<strong>${esc(asset.label)}</strong></span></button>`).join("");
  const visualPanel = relatedVisuals ? `<div class="related-visual-review"><strong>关联图表证据</strong><div>${relatedVisuals}</div></div>` : "";
  pane.innerHTML = `<header class="original-head"><span>IMMUTABLE ORIGINAL · #${row.item_id}</span><h3>${esc(row.original_meaning)}</h3></header><div class="original-grid">${fields.map(field => `<div class="original-field ${field === "context_explanation" ? "context" : ""}"><small>${fieldLabels[field]}</small><p>${esc(originalValue(row, field))}</p></div>`).join("")}</div>${visualPanel}<div class="provenance"><strong>论文定位</strong><p>PDF第 ${esc(sourcePage || "?")} 页 · ${esc(row.original_source_locator || "未标注")}<br>${esc(row.original_source_excerpt || "")}</p><button class="source-open" type="button" data-source-open="${row.item_id}">打开原文定位并高亮 →</button></div><div class="review-note-panel"><label for="selected-review-note">核验备注 <small>可选，不属于六列数据</small></label><textarea id="selected-review-note" maxlength="500" placeholder="例如：材料条件应来自表头；该值是计算量，不是直接测量。">${esc(reviewNote)}</textarea><p>仅在确认或修正时写入版本历史，并用于改进后续抽取。</p></div>`;
  pane.querySelector("[data-source-open]")?.addEventListener("click", () => openSourceViewer(row.item_id));
  pane.querySelectorAll("[data-review-related-visual]").forEach(button => button.addEventListener("click", () => {
    setReviewObject(button.dataset.reviewRelatedType);
    window.requestAnimationFrame(() => document.querySelector(`[data-visual-review="${button.dataset.reviewRelatedVisual}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" }));
  }));
  pane.querySelector("#selected-review-note")?.addEventListener("input", event => {
    const itemId = Number(row.item_id);
    const note = event.target.value.trim();
    if (note) state.reviewNotes.set(itemId, event.target.value);
    else state.reviewNotes.delete(itemId);
    markRowDirty(itemId, Boolean(note) || state.fieldDirtyRows.has(itemId));
  });
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

async function openSourceViewer(id, rowHint = null) {
  const row = rowHint || state.rows.find(item => item.item_id === id) || null;
  const dialog = document.querySelector("#source-dialog");
  const meta = document.querySelector("#source-meta");
  const focusImage = document.querySelector("#source-focus-image");
  const image = document.querySelector("#source-image");
  const pdfLink = document.querySelector("#source-open-pdf");
  meta.innerHTML = row
    ? sourceMetaLoading(row)
    : '<article class="source-meta-card"><strong>正在定位原文证据</strong><p>正在读取证据页码、原文片段和高亮位置。</p></article>';
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

function rowFieldsDirty(id) {
  const row = state.rows.find(item => Number(item.item_id) === Number(id));
  const tr = document.querySelector(`tr[data-item="${id}"]`);
  if (!row || !tr) return false;
  return fields.some(field => {
    const input = tr.querySelector(`[data-field="${field}"]`);
    return String(input?.value ?? "") !== String(row[field] ?? "");
  });
}

async function confirmRow(id, options = {}) {
  if (rejectReadOnlyAction("确认或修正数据")) return;
  setReviewRowBusy(id, true);
  try {
    const values = collectRowFields(id);
    const current = state.rows.find(row => row.item_id === id);
    const changed = fields.filter(field => String(values[field] ?? "") !== String(current[field] ?? ""));
    const reviewerNote = String(state.reviewNotes.get(Number(id)) || "").trim();
    const automaticNote = changed.length ? `修改字段：${changed.map(field => fieldLabels[field]).join("、")}` : "人工确认：内容无修改";
    const result = await api(`/api/six-data/${id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fields: values,
        editor: "本地研究者",
        note: reviewerNote ? `${automaticNote}；人工核验备注：${reviewerNote}` : automaticNote,
      }),
    });
    state.rows = state.rows.map(row => row.item_id === id ? result : row);
    clearDirtyRows(id);
    state.selected = id;
    await refreshReviewFeedback();
    renderTable();
    renderOriginal(result);
    if (options.goNext) {
      window.requestAnimationFrame(() => selectNextUnreviewed());
    }
    toast(result.review_action === "confirmation"
      ? `已确认内容无误并记录为 v${result.version_no}；自动提取原始版本未改变。`
      : `已保存修正并创建版本 v${result.version_no}；自动提取原始版本未改变。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setReviewRowBusy(id, false);
  }
}

function closeReviewDecision() {
  state.reviewDecision = null;
  document.querySelector("#review-decision-dialog")?.close();
}

function openReviewDecision(itemId, decision) {
  if (rejectReadOnlyAction("记录审核决定")) return;
  if (hasUnsavedEdits() && !confirmDiscardUnsaved("记录审核决定")) return;
  const row = state.rows.find(item => Number(item.item_id) === Number(itemId));
  if (!row || row.origin_type === "manual") return;
  state.reviewDecision = { itemId: Number(itemId), decision };
  const rejected = decision === "rejected";
  setText("review-decision-title", rejected ? "标记为不采用" : "标记为存在歧义");
  setText(
    "review-decision-description",
    rejected
      ? "这条候选不会再进入正常检索，并会作为负例帮助后续抽取避免同类错误。"
      : "这条候选会退出待审核队列，并作为需要补充证据或条件的歧义样本。",
  );
  document.querySelector("#review-decision-reason").value = "";
  document.querySelector("#review-decision-note").value = "";
  const dialog = document.querySelector("#review-decision-dialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "open");
}

async function submitReviewDecision(event) {
  event.preventDefault();
  const current = state.reviewDecision;
  if (!current) return;
  const saveButton = document.querySelector("#save-review-decision");
  const reason = document.querySelector("#review-decision-reason").value;
  const note = document.querySelector("#review-decision-note").value.trim();
  if (!reason) {
    toast("请选择主要原因。", true);
    return;
  }
  saveButton.disabled = true;
  saveButton.textContent = "正在保存…";
  try {
    const result = await api(`/api/six-data/${current.itemId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision: current.decision,
        reason_code: reason,
        note,
        editor: "本地研究者",
      }),
    });
    state.rows = state.rows.map(row => row.item_id === current.itemId ? result : row);
    clearDirtyRows(current.itemId);
    closeReviewDecision();
    await refreshReviewFeedback();
    state.selected = null;
    renderTable();
    toast(current.decision === "rejected"
      ? "已标记为不采用；该候选不会进入正常检索。"
      : "已标记为存在歧义；该候选已进入负例学习通道。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "保存审核决定";
  }
}

async function reopenReviewDecision(itemId) {
  if (rejectReadOnlyAction("恢复待审核")) return;
  const current = state.rows.find(row => Number(row.item_id) === Number(itemId));
  if (!current || current.origin_type === "manual" || current.review_action === "automatic") return;
  if (hasUnsavedEdits() && !confirmDiscardUnsaved("恢复为待审核")) return;
  const previousLabel = {
    confirmation: "已确认",
    correction: "已修正",
    rejected: "不采用",
    ambiguous: "存在歧义",
  }[current.review_action] || "已审核";
  if (!window.confirm(
    `确定将 #${itemId} 从“${previousLabel}”恢复为待审核吗？\n\n` +
    "已保存版本、自动抽取原始版本和修正历史都会保留。"
  )) return;
  setReviewRowBusy(itemId, true, "正在恢复…");
  try {
    const result = await api(`/api/six-data/${itemId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: "automatic", note: "用户恢复为待审核", editor: "本地研究者" }),
    });
    state.rows = state.rows.map(row => row.item_id === itemId ? result : row);
    clearDirtyRows();
    await refreshReviewFeedback();
    state.selected = itemId;
    renderTable();
    toast("已恢复为待审核。原始抽取版本和审核历史均已保留。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setReviewRowBusy(itemId, false);
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
  if (rejectReadOnlyAction("人工补录")) return;
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
      state.learningReport = await api(`/api/current-paper/learning-report?paper_id=${encodeURIComponent(state.paper.id)}`);
      state.audit = await api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(state.paper.id)}`);
      setText("nav-count", state.rows.length);
    }
    await refreshReviewFeedback();
    form.reset();
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
  if (rejectReadOnlyAction("导入 JSON")) return;
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

const searchModeCopy = {
  item: {
    placeholder: "例如：CoCrFeMnNi 300°C 辐照后 硬度",
    help: "检索六列数据及原文证据；同一原表的命中数据会集中显示，并支持展开全部。",
    suggestions: ["CoCrFeMnNi 300°C 辐照后 硬度", "钨 辐照温度", "Table 3"],
  },
  table: {
    placeholder: "例如：三种材料 辐照前后 纳米硬度",
    help: "以完整表格为单位检索物理量、材料、实验条件、方法、表题和正文解释。",
    suggestions: ["纳米硬度", "材料成分 EDS", "热力学参数"],
  },
  figure: {
    placeholder: "例如：位错环密度 随剂量变化 300°C",
    help: "以完整图片为单位检索坐标变量、材料、条件、图注和正文结论；不会自动猜读曲线点。",
    suggestions: ["位错环密度 随剂量", "SRIM 损伤深度", "纳米压痕 载荷 位移"],
  },
  finding: {
    placeholder: "例如：未观察到空洞 辐照后 相稳定",
    help: "单独检索不适合作为数值的数据结论，例如趋势、比较、存在、缺失和显微观察。",
    suggestions: ["未观察到 空洞", "硬度 随温度降低", "辐照后 相变"],
  },
};

function itemSearchParams(q) {
  return new URLSearchParams({
    q,
    review: state.searchFilters.review,
    source: state.searchFilters.source,
    sort: state.searchFilters.sort,
  }).toString();
}

function setSearchBusy(busy) {
  const results = document.querySelector("#search-results");
  results.classList.toggle("is-loading", busy);
  if (busy) {
    results.innerHTML = Array.from({ length: 3 }, () => '<div class="search-skeleton"><i></i><span></span><span></span></div>').join("");
    setText("search-summary", "正在检索证据库…");
  }
}

function recentSearches() {
  try {
    return JSON.parse(localStorage.getItem("evidenceRecentSearches") || "[]").filter(item => item && item.query && searchModeCopy[item.mode]);
  } catch (_) {
    return [];
  }
}

function rememberSearch(query) {
  if (!query) return;
  const next = [{ mode: state.searchMode, query }, ...recentSearches().filter(item => !(item.mode === state.searchMode && item.query === query))].slice(0, 12);
  try { localStorage.setItem("evidenceRecentSearches", JSON.stringify(next)); } catch (_) { /* local history is optional */ }
  renderRecentSearches();
}

function useSearchQuery(query, remember = true) {
  const input = document.querySelector("#search-query");
  input.value = query;
  document.querySelector("#search-clear").hidden = !query;
  runSearch(null, { remember });
}

function renderSearchSuggestions() {
  const suggestions = searchModeCopy[state.searchMode].suggestions || [];
  const container = document.querySelector("#search-suggestions");
  container.innerHTML = suggestions.map(query => `<button type="button" data-search-suggestion="${esc(query)}">${esc(query)}</button>`).join("");
  container.querySelectorAll("[data-search-suggestion]").forEach(button => button.addEventListener("click", () => useSearchQuery(button.dataset.searchSuggestion)));
  renderRecentSearches();
}

function renderRecentSearches() {
  const wrapper = document.querySelector("#recent-searches");
  const container = document.querySelector("#recent-search-items");
  const items = recentSearches().filter(item => item.mode === state.searchMode).slice(0, 4);
  wrapper.hidden = !items.length;
  container.innerHTML = items.map(item => `<button type="button" data-recent-search="${esc(item.query)}">${esc(item.query)}</button>`).join("");
  container.querySelectorAll("[data-recent-search]").forEach(button => button.addEventListener("click", () => useSearchQuery(button.dataset.recentSearch, false)));
}

function activeFilterLabels() {
  const labels = [];
  if (state.searchFilters.review === "reviewed") labels.push("仅人工核验");
  if (state.searchFilters.review === "pending") labels.push("仅待审核");
  const sourceLabels = { text: "正文数据", table: "表格数据", figure: "图片关联", manual: "人工补录" };
  if (sourceLabels[state.searchFilters.source]) labels.push(sourceLabels[state.searchFilters.source]);
  const sortLabels = { source_page: "按文章与页码", article: "按文章归类" };
  if (sortLabels[state.searchFilters.sort]) labels.push(sortLabels[state.searchFilters.sort]);
  return labels;
}

function renderActiveFilters() {
  const container = document.querySelector("#search-active-filters");
  const labels = state.searchMode === "item" ? activeFilterLabels() : [];
  container.innerHTML = labels.map(label => `<span>${esc(label)}</span>`).join("");
}

function resetSearchWorkspace() {
  state.searchFilters = { review: "all", source: "all", sort: "relevance" };
  document.querySelector("#search-review-filter").value = "all";
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-sort").value = "relevance";
  document.querySelector("#search-query").value = "";
  document.querySelector("#search-clear").hidden = true;
  renderActiveFilters();
  runSearch(null, { remember: false });
}

let searchInputTimer = null;
function scheduleSearchFromInput() {
  const input = document.querySelector("#search-query");
  document.querySelector("#search-clear").hidden = !input.value;
  if (state.searchComposing) return;
  clearTimeout(searchInputTimer);
  searchInputTimer = setTimeout(() => runSearch(null, { remember: false }), 420);
}

function focusSearchShortcut(event) {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target?.isContentEditable) return;
  if (document.body.dataset.view !== "search") return;
  event.preventDefault();
  document.querySelector("#search-query").focus();
}

async function runSearch(event, options = {}) {
  event?.preventDefault();
  const q = document.querySelector("#search-query").value.trim();
  const requestId = ++state.searchRequest;
  state.search = q;
  document.querySelector("#search-clear").hidden = !q;
  setSearchBusy(true);
  renderActiveFilters();
  try {
    if (state.searchMode === "item") {
      const params = itemSearchParams(q);
      const result = await api(`/api/six-search?${params}&meta=1`);
      if (requestId !== state.searchRequest) return;
      const rows = result.rows;
      state.searchResults = rows;
      const countText = result.total > rows.length
        ? `显示前 ${rows.length} 条，共 ${result.total} 条`
        : `${rows.length} 个物理事实`;
      setText("search-summary", q ? `“${q}” · ${countText}` : `最近收录 · ${countText}`);
      document.querySelector("#search-export").href = `/api/six-export.csv?${params}`;
      document.querySelector("#search-export-xlsx").href = `/api/six-export.xlsx?${params}`;
      renderResults(rows);
    } else if (state.searchMode === "finding") {
      const result = await api(`/api/qualitative-search?q=${encodeURIComponent(q)}&limit=100`);
      if (requestId !== state.searchRequest) return;
      const rows = result.rows || [];
      state.searchResults = rows;
      const countText = result.total > rows.length
        ? `显示前 ${rows.length} 条，共 ${result.total} 条`
        : `${rows.length} 条结论`;
      setText("search-summary", q ? `“${q}” · ${countText}` : `当前收录 · ${countText}`);
      document.querySelector("#search-export").href = `/api/qualitative-export.csv?q=${encodeURIComponent(q)}`;
      document.querySelector("#search-export-xlsx").href = `/api/qualitative-export.xlsx?q=${encodeURIComponent(q)}`;
      renderQualitativeResults(rows);
    } else {
      const assets = await api(`/api/visual-search?type=${encodeURIComponent(state.searchMode)}&q=${encodeURIComponent(q)}`);
      if (requestId !== state.searchRequest) return;
      state.searchResults = assets;
      const label = state.searchMode === "table" ? "张原始表格" : "幅论文图片";
      setText("search-summary", q ? `“${q}” · ${assets.length} ${label}` : `当前收录 ${assets.length} ${label}`);
      renderVisualResults(assets);
    }
    if (options.remember ?? Boolean(event)) rememberSearch(q);
  } catch (error) {
    if (requestId !== state.searchRequest) return;
    document.querySelector("#search-results").innerHTML = `<div class="blank search-error"><h3>检索暂时不可用</h3><p>${esc(error.message)}</p></div>`;
    toast(error.message, true);
  } finally {
    if (requestId === state.searchRequest) document.querySelector("#search-results").classList.remove("is-loading");
  }
}

function setSearchMode(mode, options = {}) {
  if (!searchModeCopy[mode]) return;
  state.searchMode = mode;
  document.querySelectorAll("[data-search-mode]").forEach(button => {
    const active = button.dataset.searchMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  const input = document.querySelector("#search-query");
  input.placeholder = searchModeCopy[mode].placeholder;
  setText("search-help", searchModeCopy[mode].help);
  document.querySelector("#search-exports").hidden = !["item", "finding"].includes(mode);
  document.querySelector("#search-filter-bar").hidden = mode !== "item";
  renderSearchSuggestions();
  renderActiveFilters();
  if (options.run !== false) runSearch();
}

function searchReviewLabel(row) {
  if (row.origin_type === "manual") return "人工补录";
  return {
    confirmation: "已确认",
    correction: "已修正",
    automatic: "待审核",
  }[row.review_action] || "待审核";
}

function sourceLabel(row) {
  const primary = row.primary_visual_asset;
  if (row.source_kind === "table" && primary) return `表格数据 · ${primary.label}`;
  if (row.source_kind === "figure" && primary) return `图片数据 · ${primary.label}`;
  const relatedFigure = (row.visual_assets || []).find(asset => asset.asset_type === "figure");
  if (relatedFigure) return `正文数据 · 关联 ${relatedFigure.label}`;
  if (row.source_kind === "manual") return "人工补录";
  return "正文数据";
}

function brief(value, maxLength = 240) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength).trim()}…` : text;
}

function highlightSearchText(value) {
  const text = String(value ?? "");
  const terms = state.search.split(/\s+/).map(term => term.trim()).filter(term => term.length > 1);
  if (!terms.length) return esc(text);
  const escapedTerms = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escapedTerms.join("|")})`, "gi");
  return text.split(pattern).map(part => terms.some(term => part.toLocaleLowerCase() === term.toLocaleLowerCase()) ? `<mark>${esc(part)}</mark>` : esc(part)).join("");
}

function itemResultHtml(row) {
  const authors = [row.first_author ? `一作：${row.first_author}` : "", row.corresponding_author ? `通讯：${row.corresponding_author}` : ""].filter(Boolean).join(" · ");
  const reviewLabel = searchReviewLabel(row);
  const reviewButton = isReadOnly() ? "" : `<button class="row-link" data-jump="${row.item_id}">去校对</button>`;
  const sourceButton = `<button class="row-link source-link" data-source-search="${row.item_id}">原文证据</button>`;
  const linked = row.primary_visual_asset || (row.visual_assets || []).find(asset => asset.asset_type === "figure");
  const visualButton = linked
    ? `<button class="row-link visual-link" data-visual-open="${linked.id}">${linked.asset_type === "table" ? "查看原始表格" : "查看相关图片"}</button>`
    : "";
  const page = row.source_page || row.original_source_page;
  const scoreTitle = row.search_score != null ? ` title="内部匹配得分 ${esc(row.search_score)}"` : "";
  const excerpt = brief(row.source_excerpt || row.original_source_excerpt);
  const clusterBadge = Number(row.fact_cluster_size || 1) > 1
    ? `<span class="fact-cluster-badge" title="${esc((row.fact_member_ids || []).join("、"))}">${esc(row.fact_cluster_size)} 条重复记录已合并 · ${esc(row.evidence_count || 1)} 处证据</span>`
    : `<span class="fact-cluster-badge single">${esc(row.evidence_count || 1)} 处证据</span>`;
  return `<article class="result" data-item-result="${row.item_id}">
    <div class="result-value-block"><small>报告值</small><div class="value">${highlightSearchText(row.value_text)}<span>${esc(row.unit)}</span></div><span class="source-kind ${esc(row.source_kind)}">${esc(sourceLabel(row))}</span></div>
    <div class="result-content">
      <div class="result-heading"><div><small>具体意义</small><h3>${highlightSearchText(row.meaning)}</h3>${clusterBadge}</div><em class="search-review-state ${esc(row.review_action || row.origin_type)}"${scoreTitle}>${esc(reviewLabel)}</em></div>
      <div class="result-context"><small>实验条件与文章语境</small><p>${highlightSearchText(row.context_explanation)}</p></div>
      ${excerpt ? `<blockquote><span>原文证据</span><p>${highlightSearchText(excerpt)}</p></blockquote>` : ""}
      <footer><div class="result-paper"><strong>${highlightSearchText(row.article_title)}</strong><span>${[row.doi, authors, page ? `PDF 第 ${page} 页` : ""].filter(Boolean).map(esc).join(" · ")}</span></div><div class="result-actions">${visualButton}${sourceButton}${reviewButton}</div></footer>
    </div>
  </article>`;
}

function renderQualitativeResults(rows) {
  const el = document.querySelector("#search-results");
  if (!rows.length) {
    el.innerHTML = `<div class="blank search-empty"><span>⌕</span><h3>没有找到相关实验结论</h3><p>可以尝试材料、缺陷类型、趋势词或“观察到/未观察到”等表达。</p><button type="button" data-clear-empty>清除关键词，浏览全部</button></div>`;
    el.querySelector("[data-clear-empty]")?.addEventListener("click", resetSearchWorkspace);
    return;
  }
  el.innerHTML = rows.map(row => {
    const page = row.source_page || row.original_source_page;
    const evidence = brief(row.source_excerpt || row.original_source_excerpt);
    const merged = Number(row.finding_cluster_size || 1) > 1
      ? `${row.finding_cluster_size} 条近义结论已合并 · ${row.evidence_count || 1} 处证据`
      : `${row.evidence_count || 1} 处证据`;
    return `<article class="result qualitative-result" data-item-result="${row.item_id}">
      <div class="finding-mark"><small>QUALITATIVE</small><span>实验结论</span></div>
      <div class="result-content">
        <div class="result-heading"><div><small>具体意义</small><h3>${highlightSearchText(row.meaning)}</h3><span class="fact-cluster-badge">${esc(merged)}</span></div></div>
        <div class="finding-text"><small>论文报告的结论</small><p>${highlightSearchText(row.finding_text)}</p></div>
        <div class="result-context"><small>实验条件与文章语境</small><p>${highlightSearchText(row.context_explanation)}</p></div>
        ${evidence ? `<blockquote><span>原文证据</span><p>${highlightSearchText(evidence)}</p></blockquote>` : ""}
        <footer><div class="result-paper"><strong>${highlightSearchText(row.article_title)}</strong><span>${[row.doi, page ? `PDF 第 ${page} 页` : ""].filter(Boolean).map(esc).join(" · ")}</span></div><div class="result-actions"><button class="row-link source-link" data-source-finding="${row.item_id}">原文证据</button></div></footer>
      </div>
    </article>`;
  }).join("");
  el.querySelectorAll("[data-source-finding]").forEach(button => button.addEventListener("click", () => {
    const itemId = Number(button.dataset.sourceFinding);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
}

function renderResults(rows) {
  const el = document.querySelector("#search-results");
  if (!rows.length) {
    el.innerHTML = `<div class="blank search-empty"><span>⌕</span><h3>没有找到直接匹配的数据</h3><p>减少一个条件，或改用材料名称、元素符号、物理量和测试方法重新组合。</p><button type="button" data-clear-empty>清除条件，浏览最近数据</button></div>`;
    el.querySelector("[data-clear-empty]")?.addEventListener("click", resetSearchWorkspace);
    return;
  }
  const tableGroups = new Map();
  rows.forEach(row => {
    const table = row.source_kind === "table" ? row.primary_visual_asset : null;
    if (!table) return;
    if (!tableGroups.has(Number(table.id))) tableGroups.set(Number(table.id), { asset: table, rows: [] });
    tableGroups.get(Number(table.id)).rows.push(row);
  });
  const renderedTables = new Set();
  const html = [];
  rows.forEach(row => {
    const table = row.source_kind === "table" ? row.primary_visual_asset : null;
    if (!table) {
      html.push(itemResultHtml(row));
      return;
    }
    const assetId = Number(table.id);
    if (renderedTables.has(assetId)) return;
    renderedTables.add(assetId);
    const group = tableGroups.get(assetId);
    const visible = group.rows.slice(0, 3);
    const hidden = group.rows.slice(3);
    html.push(`<section class="table-result-group"><header><div><span>${esc(group.asset.label)} · 同一原表</span><h3>${highlightSearchText(group.asset.display_name || group.asset.label)} · ${group.rows.length} 条匹配数据</h3><p>${highlightSearchText(group.asset.context_explanation || group.asset.caption || "表格来源数据")}</p></div><button type="button" data-visual-open="${group.asset.id}">查看完整原表</button></header><div class="table-group-visible">${visible.map(itemResultHtml).join("")}</div>${hidden.length ? `<details><summary>展开其余 ${hidden.length} 条数据</summary><div>${hidden.map(itemResultHtml).join("")}</div></details>` : ""}</section>`);
  });
  el.innerHTML = html.join("");
  el.querySelectorAll("[data-jump]").forEach(btn => btn.addEventListener("click", () => jumpToRow(Number(btn.dataset.jump))));
  el.querySelectorAll("[data-source-search]").forEach(btn => btn.addEventListener("click", () => {
    const itemId = Number(btn.dataset.sourceSearch);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
  el.querySelectorAll("[data-visual-open]").forEach(btn => btn.addEventListener("click", () => openVisualAsset(Number(btn.dataset.visualOpen))));
}

function renderVisualResults(assets) {
  const el = document.querySelector("#search-results");
  if (!assets.length) {
    el.innerHTML = `<div class="blank search-empty"><span>⌕</span><h3>没有找到相关${state.searchMode === "table" ? "表格" : "图片"}</h3><p>尝试物理量、材料、实验条件、测试方法或变量关系。</p><button type="button" data-clear-empty>清除关键词，浏览全部</button></div>`;
    el.querySelector("[data-clear-empty]")?.addEventListener("click", resetSearchWorkspace);
    return;
  }
  el.innerHTML = assets.map(asset => {
    const typeLabel = asset.asset_type === "table" ? "完整表格" : "完整图片";
    const tags = (asset.tags || []).slice(0, 7).map(value => `<span>${esc(value)}</span>`).join("");
    const materials = (asset.materials || []).join(" · ");
    const facts = [
      ["材料", materials], ["条件", asset.conditions_text], ["方法", asset.methods_text],
    ].filter(([, value]) => String(value || "").trim());
    const factHtml = facts.length ? `<dl>${facts.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${highlightSearchText(value)}</dd></div>`).join("")}</dl>` : "";
    return `<article class="visual-result"><button class="visual-thumb visual-thumb-${esc(asset.asset_type)}" type="button" data-visual-open="${asset.id}" aria-label="查看${esc(asset.display_name || asset.label)}"><img src="${esc(asset.image_url)}" alt="${esc(asset.display_name || asset.label)}原文截图" loading="lazy"><span>${esc(typeLabel)}</span></button><div class="visual-result-copy"><div class="visual-result-title"><span>${esc(asset.label)} · PDF第 ${esc(asset.page_start)} 页</span><h3>${highlightSearchText(asset.display_name || asset.label)}</h3></div><div class="visual-quantity-list">${tags}</div><p>${highlightSearchText(asset.context_explanation || "待核对原文上下文。")}</p>${factHtml}<small>${esc(asset.article_title)} · ${esc(asset.doi || "无 DOI")}</small><button class="open-visual" type="button" data-visual-open="${asset.id}">查看完整${asset.asset_type === "table" ? "表格" : "图片"}</button></div></article>`;
  }).join("");
  el.querySelectorAll("[data-visual-open]").forEach(btn => btn.addEventListener("click", () => openVisualAsset(Number(btn.dataset.visualOpen))));
}

function formatVisualVariables(variables) {
  return Object.entries(variables || {}).map(([key, value]) => `${key}：${value}`).join("；");
}

async function openVisualAsset(assetId) {
  const dialog = document.querySelector("#visual-dialog");
  setText("visual-dialog-title", "正在读取完整图表…");
  document.querySelector("#visual-image").removeAttribute("src");
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute("open", "open");
  }
  try {
    const asset = await api(`/api/visual-assets/${assetId}`);
    state.visualAsset = asset;
    setText("visual-dialog-type", asset.asset_type === "table" ? "ORIGINAL TABLE" : "ORIGINAL FIGURE");
    setText("visual-dialog-title", `${asset.display_name || asset.label} · ${asset.label} · PDF第 ${asset.page_start} 页`);
    const image = document.querySelector("#visual-image");
    image.src = `${asset.image_url}?ts=${Date.now()}`;
    image.alt = `${asset.display_name || asset.label}高分辨率原文截图`;
    setText("visual-caption", asset.caption);
    setText("visual-context", asset.context_explanation || "尚未生成结构化解释。");
    const details = [
      ["物理量", (asset.physical_quantities || []).join("、")],
      ["变量/表头", formatVisualVariables(asset.variables)],
      ["材料/样品", (asset.materials || []).join("、")],
      ["实验条件", asset.conditions_text],
      ["方法", asset.methods_text],
      ["关联条目", `${asset.linked_item_count || 0} 条`],
    ].filter(([, value]) => String(value || "").trim());
    document.querySelector("#visual-details").innerHTML = details.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
    setText("visual-source-context", asset.source_context || asset.caption);
    setText("visual-paper", `${asset.article_title} · ${asset.doi} · PDF第 ${asset.page_start} 页`);
    document.querySelector("#visual-tags").innerHTML = (asset.tags || []).map(tag => `<span>${esc(tag)}</span>`).join("");
    document.querySelector("#visual-open-pdf").href = asset.pdf_url;
    const related = document.querySelector("#visual-related-items");
    related.textContent = `查看相关数据条目（${asset.linked_item_count || 0}）`;
    related.disabled = !asset.linked_item_count;
  } catch (error) {
    setText("visual-dialog-title", "图表加载失败");
    setText("visual-context", error.message);
    toast(error.message, true);
  }
}

function closeVisualAsset() {
  state.visualAsset = null;
  document.querySelector("#visual-dialog")?.close();
}

function showVisualRelatedItems() {
  const asset = state.visualAsset;
  if (!asset) return;
  closeVisualAsset();
  state.searchFilters = { review: "all", source: "all", sort: "relevance" };
  document.querySelector("#search-review-filter").value = "all";
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-sort").value = "relevance";
  document.querySelector("#search-query").value = asset.label;
  setSearchMode("item", { run: false });
  runSearch();
}

async function jumpToRow(id) {
  if (isReadOnly()) {
    openSourceViewer(id);
    return;
  }
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
  const reportLink = document.querySelector("#learning-report-export");
  renderLearningGuidanceCard();
  if (summary && exportLink && exportAllLink && reportLink) {
    const learning = state.learning || { sample_count: 0, correction_count: 0, confirmation_count: 0, manual_count: 0, rejected_count: 0, ambiguous_count: 0 };
    const allLearning = state.allLearning || { sample_count: 0 };
    const report = state.learningReport || { included_in_prompt: false };
    summary.textContent = learning.sample_count
      ? `当前文章已有 ${learning.sample_count} 条学习样本：确认 ${learning.confirmation_count}、修正 ${learning.correction_count}、歧义 ${learning.ambiguous_count || 0}、不采用 ${learning.rejected_count || 0}、补录 ${learning.manual_count}。${report.included_in_prompt ? "下一次抽取会加入学习提示。" : "下一次抽取暂无学习提示。"}全库共 ${allLearning.sample_count || 0} 条。`
      : `当前文章还没有学习样本；全库共 ${allLearning.sample_count || 0} 条。确认修正或人工补录后，这里会自动累积。`;
    exportLink.href = `/api/current-paper/learning-samples.jsonl?paper_id=${encodeURIComponent(state.paper?.id || "")}`;
    exportLink.classList.toggle("disabled", !learning.sample_count);
    exportLink.setAttribute("aria-disabled", learning.sample_count ? "false" : "true");
    exportAllLink.href = "/api/learning-samples.jsonl";
    exportAllLink.classList.toggle("disabled", !allLearning.sample_count);
    exportAllLink.setAttribute("aria-disabled", allLearning.sample_count ? "false" : "true");
    reportLink.href = `/api/current-paper/learning-report.md?paper_id=${encodeURIComponent(state.paper?.id || "")}`;
    reportLink.classList.toggle("disabled", !learning.sample_count);
    reportLink.setAttribute("aria-disabled", learning.sample_count ? "false" : "true");
  }
  if (!changed.length) {
    el.innerHTML = '<div class="blank"><h3>还没有已确认修正</h3><p>左侧表格里的临时输入不会出现在这里。</p></div>';
    return;
  }
  const historyLabels = { confirmation: "确认无误", correction: "已修正", rejected: "不采用", ambiguous: "存在歧义", automatic: "恢复待审核" };
  el.innerHTML = changed.map(row => `<article class="history-card"><span>${row.origin_type === "manual" ? "人工补录" : `${historyLabels[row.review_action] || "审核记录"} v${row.version_no}`}</span><div><strong>${esc(row.meaning)} · ${esc(row.value_text)} ${esc(row.unit)}</strong><p>${esc(row.context_explanation)}</p></div><div><strong>${esc(row.editor)}</strong><p>${esc(row.edit_note || "")}<br>${esc(row.created_at)}</p></div></article>`).join("");
}

function renderLearningGuidanceCard() {
  const el = document.querySelector("#learning-guidance-card");
  if (!el) return;
  const report = state.learningReport || { sample_count: 0, included_samples: [] };
  const allReport = state.allLearningReport || { sample_count: 0 };
  const readinessLabels = {
    empty: "暂无学习提示",
    confirmations_only: "已有正例",
    useful: "可用于优化抽取",
  };
  const sampleTypeLabels = { correction: "修正", confirmation: "确认", manual_addition: "人工补录", rejection: "不采用", ambiguity: "存在歧义" };
  const included = (report.included_samples || []).slice(0, 3).map(sample => {
    const changed = (sample.changed_labels || []).join("、") || "确认无误";
    return `<li><strong>${esc(sampleTypeLabels[sample.sample_type] || sample.sample_type)} · #${esc(sample.item_id)}</strong><span>${esc(changed)}</span><p>${esc(sample.corrected_meaning || "")}；${esc(sample.corrected_context || "")}</p></li>`;
  }).join("");
  const preview = report.guidance_preview
    ? `<details><summary>查看将加入 DeepSeek 提示的学习片段</summary><pre>${esc(report.guidance_preview)}</pre></details>`
    : `<p class="learning-empty">还没有可加入提示的人工学习样本。</p>`;
  const negativeCount = Number(report.rejected_count || 0) + Number(report.ambiguous_count || 0);
  el.innerHTML = `<div><span>${esc(readinessLabels[report.readiness] || "学习状态")}</span><h3>${esc(report.message || "等待人工校对样本")}</h3><p>${esc(report.safety_rule || "学习样本只用于字段边界、失败模式和措辞偏好；不能作为新论文数据证据。")}</p></div><dl><div><dt>当前文章样本</dt><dd>${esc(report.sample_count || 0)}</dd></div><div><dt>负例/歧义</dt><dd>${esc(negativeCount)}</dd></div><div><dt>全库样本</dt><dd>${esc(allReport.sample_count || 0)}</dd></div><div><dt>进入提示</dt><dd>${esc(report.included_sample_count || 0)}</dd></div></dl><ul>${included || "<li><strong>尚无样本</strong><span>完成任一审核决定后自动出现</span></li>"}</ul>${preview}`;
}

function switchView(name) {
  if (isReadOnly() && name !== "search") name = "search";
  if (name !== "review" && state.focusReview) setFocusReview(false);
  document.querySelectorAll(".nav,.view").forEach(el => el.classList.remove("active"));
  document.querySelector(`.nav[data-view="${name}"]`)?.classList.add("active");
  document.querySelector(`#view-${name}`).classList.add("active");
  document.body.dataset.view = name;
  renderViewHeader(name);
  if (name === "search" && !document.querySelector("#search-results").children.length) runSearch();
  if (name === "upload") refreshUploadWorkspace();
  if (name === "manual") fillManualDefaults();
}

function setFocusReview(enabled) {
  const active = Boolean(enabled) && document.body.dataset.view === "review" && !isReadOnly();
  state.focusReview = active;
  document.body.dataset.focusReview = active ? "true" : "false";
  const button = document.querySelector("#focus-review");
  const bar = document.querySelector("#focus-review-bar");
  if (button) {
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.textContent = active ? "退出专注" : "专注校对";
  }
  if (bar) bar.hidden = !active;
  if (active) document.querySelector("#view-review")?.scrollIntoView({ block: "start" });
}

async function refreshUploadWorkspace() {
  try {
    [state.uploads, state.jobs, state.ai] = await Promise.all([
      api("/api/uploads"), api("/api/processing-jobs"), api("/api/ai/status"),
    ]);
    state.mineru = state.ai?.mineru || null;
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

function renderReviewProgressCard(progress) {
  const total = progress.total || 0;
  const ratio = total ? Math.round((progress.reviewed / total) * 100) : 0;
  setText("review-progress-title", `${progress.reviewed}/${total} 已审核 · ${ratio}%`);
  setText("focus-review-progress", `${progress.reviewed}/${total} 已审核 · ${progress.unreviewed} 待审核`);
  setText("review-progress-unreviewed", progress.unreviewed);
  setText("review-progress-confirmed", progress.confirmed);
  setText("review-progress-corrected", progress.corrected);
  setText("review-progress-ambiguous", progress.ambiguous);
  setText("review-progress-rejected", progress.rejected);
  setText("review-progress-manual", progress.manual);
  const bar = document.querySelector("#review-progress-bar");
  if (bar) bar.style.width = `${ratio}%`;
  const attention = Number(state.audit?.review_priority_counts?.unreviewed_attention || 0);
  const calibrationRemaining = state.rows.filter(row => state.calibrationReviewIds.has(Number(row.item_id)) && isUnreviewedRow(row)).length;
  const note = state.calibrationActive
    ? calibrationRemaining
      ? `分层校准进行中：本轮剩余 ${calibrationRemaining}/${state.calibrationBatchTotal} 条。样本覆盖正文、表格、图片关联和不同数值形态。`
      : `本轮分层校准已完成。退出校准模式后可继续审核其余数据。`
    : calibrationRemaining
      ? `本机保存了一轮未完成的分层校准，剩余 ${calibrationRemaining}/${state.calibrationBatchTotal} 条；点击“继续本轮校准”恢复。`
    : progress.unreviewed
      ? attention
        ? `建议先核验 ${attention} 条重点项；系统只按证据定位强弱排序，不代表这些数据一定有误。`
        : `下一步：点击“下一条未审核”逐条核验，或下载待审核清单分批处理。`
      : `当前文章已无未审核自动抽取数据，可进入搜索与学习样本复查。`;
  setText("review-progress-note", note);
}

async function uploadPdf(event) {
  event.preventDefault();
  if (rejectReadOnlyAction("上传文献")) return;
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
  if (rejectReadOnlyAction("切换当前校对文章")) return false;
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
  if (rejectReadOnlyAction("自动提取或核验")) return;
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
      const visuals = result.visual_evidence || result.action_result?.visual_evidence || {};
      finishProgress(`完成：证据核验通过 ${result.action_result?.verified_count ?? 0} 条；图表 ${visuals.table_count || 0} 张表 / ${visuals.figure_count || 0} 幅图。`);
      toast(`DeepSeek 处理完成：通过 ${result.action_result?.verified_count ?? 0} 条；已建立 ${visuals.table_count || 0} 张表和 ${visuals.figure_count || 0} 幅图。`);
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
  if (rejectReadOnlyAction("生成数据快照")) return;
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
  if (rejectReadOnlyAction("切换当前校对文章")) return;
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
    rememberRecentPaper(paperId);
    toast(`已切换到《${state.paper.title || "未命名文章"}》。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

document.querySelectorAll(".nav").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
document.querySelectorAll("[data-review-object]").forEach(button => button.addEventListener("click", () => setReviewObject(button.dataset.reviewObject)));
document.querySelector("#paper-picker-query").addEventListener("input", event => {
  state.paperFilters.query = event.target.value;
  renderPaperOptions();
});
document.querySelector("#paper-author-filter").addEventListener("input", event => {
  state.paperFilters.author = event.target.value;
  renderPaperOptions();
});
document.querySelectorAll("[data-paper-topic]").forEach(button => button.addEventListener("click", () => {
  state.paperFilters.topic = button.dataset.paperTopic;
  renderPaperOptions();
}));
document.querySelector("#paper-status-filter").addEventListener("change", event => {
  state.paperFilters.status = event.target.value;
  renderPaperOptions();
});
document.querySelectorAll("[data-paper-scope]").forEach(button => button.addEventListener("click", () => {
  applyPaperScopePreset(button.dataset.paperScope);
}));
document.querySelector("#paper-filter-reset").addEventListener("click", () => {
  applyPaperScopePreset("all");
  document.querySelector("#paper-picker-query").focus();
});
document.querySelector("#paper-switch-input").addEventListener("change", renderPaperSelectionMeta);
document.querySelector("#table-filter").addEventListener("input", event => {
  state.filter = event.target.value;
  state.reviewVisibleLimit = reviewPageSize;
  renderTable();
});
document.querySelector("#review-filter").addEventListener("change", event => {
  if (event.target.value === "calibration" && !state.calibrationReviewIds.size) {
    event.target.value = state.reviewFilter;
    toggleCalibrationReview();
    return;
  }
  state.reviewFilter = event.target.value;
  state.calibrationActive = event.target.value === "calibration";
  state.selected = null;
  state.reviewVisibleLimit = reviewPageSize;
  renderTable();
});
document.querySelector("#review-sort").addEventListener("change", event => {
  state.reviewSort = event.target.value;
  state.selected = null;
  state.reviewVisibleLimit = reviewPageSize;
  renderTable();
});
document.querySelector("#next-unreviewed").addEventListener("click", selectNextUnreviewed);
document.querySelector("#review-load-more").addEventListener("click", () => {
  state.reviewVisibleLimit += reviewPageSize;
  renderTable();
});
document.querySelector("#review-calibration-start").addEventListener("click", toggleCalibrationReview);
document.querySelector("#focus-review").addEventListener("click", () => setFocusReview(!state.focusReview));
document.querySelector("#exit-focus-review").addEventListener("click", () => setFocusReview(false));
document.addEventListener("keydown", handleReviewKeyboard);
document.querySelector("#search-form").addEventListener("submit", runSearch);
document.querySelectorAll("[data-search-mode]").forEach(button => button.addEventListener("click", () => setSearchMode(button.dataset.searchMode)));
document.querySelector("#search-query").addEventListener("input", scheduleSearchFromInput);
document.querySelector("#search-query").addEventListener("compositionstart", () => { state.searchComposing = true; });
document.querySelector("#search-query").addEventListener("compositionend", () => { state.searchComposing = false; scheduleSearchFromInput(); });
document.querySelector("#search-clear").addEventListener("click", () => {
  document.querySelector("#search-query").value = "";
  document.querySelector("#search-clear").hidden = true;
  document.querySelector("#search-query").focus();
  runSearch(null, { remember: false });
});
document.querySelector("#search-review-filter").addEventListener("change", event => { state.searchFilters.review = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-source-filter").addEventListener("change", event => { state.searchFilters.source = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-sort").addEventListener("change", event => { state.searchFilters.sort = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-reset-filters").addEventListener("click", () => {
  state.searchFilters = { review: "all", source: "all", sort: "relevance" };
  document.querySelector("#search-review-filter").value = "all";
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-sort").value = "relevance";
  renderActiveFilters();
  runSearch(null, { remember: false });
});
document.addEventListener("keydown", focusSearchShortcut);
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
document.querySelector("#run-cloud-visual")?.addEventListener("click", runCloudVisual);
document.querySelector("#visual-processing-mode")?.addEventListener("change", event => changeVisualProcessingMode(event.target.value));
document.querySelector("#review-decision-form").addEventListener("submit", submitReviewDecision);
document.querySelectorAll("[data-close-review-decision]").forEach(button => button.addEventListener("click", closeReviewDecision));
document.querySelector("#review-decision-dialog")?.addEventListener("click", event => {
  if (event.target === event.currentTarget) closeReviewDecision();
});
document.querySelector("#review-decision-dialog")?.addEventListener("close", () => { state.reviewDecision = null; });
document.querySelector("[data-close-source]")?.addEventListener("click", () => document.querySelector("#source-dialog")?.close());
document.querySelector("#source-dialog")?.addEventListener("click", event => {
  const dialog = event.currentTarget;
  if (event.target === dialog) dialog.close();
});
document.querySelectorAll("[data-close-visual]").forEach(button => button.addEventListener("click", closeVisualAsset));
document.querySelector("#visual-dialog")?.addEventListener("click", event => {
  if (event.target === event.currentTarget) closeVisualAsset();
});
document.querySelector("#visual-dialog")?.addEventListener("close", () => { state.visualAsset = null; });
document.querySelector("#visual-related-items")?.addEventListener("click", showVisualRelatedItems);
window.addEventListener("beforeunload", event => {
  if (!hasUnsavedEdits()) return;
  event.preventDefault();
  event.returnValue = "";
});
renderSearchSuggestions();
renderActiveFilters();
load().catch(error => {
  state.runtimeWarnings.set("核心服务", `核心服务：${error.message}`);
  renderRuntimeWarnings();
  setText("workspace-title", "项目服务未完全连接");
  setText("workspace-subtitle", "请确认本地服务正在运行，然后刷新页面。数据库不会因页面加载失败而改变。");
  toast(`页面载入失败：${error.message}`, true);
});
