const state = { paper: null, papers: [], rows: [], selected: null, filter: "", reviewFilter: "all", reviewSort: "review_priority", search: "", searchMode: "item", visualAsset: null, extraction: null, experimentProfile: null, learning: null, allLearning: null, learningReport: null, allLearningReport: null, audit: null, deepseekRun: null, uploads: [], jobs: [], ai: null, uiMode: { read_only: false }, dirtyRows: new Set(), progressTimer: null, progressValue: 0, focusReview: false, reviewDecision: null };
const fields = ["value_text", "meaning", "unit", "article_title", "doi", "context_explanation"];
const viewCopy = {
  review: { kicker: "EVIDENCE REVIEW", title: "校对实验数据", subtitle: "逐条核对抽取结果，并随时返回原文证据。" },
  search: { kicker: "DATABASE SEARCH", title: "搜索实验数据与图表", subtitle: "在条目、完整表格和完整图片之间切换，并用同一个关键词搜索。" },
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

function isReadOnly() {
  return Boolean(state.uiMode?.read_only);
}

function applyUiMode() {
  const readonly = isReadOnly();
  document.body.dataset.readonly = readonly ? "true" : "false";
  const badge = document.querySelector("#readonly-badge");
  if (badge) badge.hidden = !readonly;
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
  state.uiMode = await api("/api/ui-mode");
  applyUiMode();
  if (isReadOnly()) {
    renderPublicSearchOnlyMode();
    switchView("search");
    return;
  }
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
  [state.paper, state.rows, state.extraction, state.experimentProfile, state.learning, state.allLearning, state.learningReport, state.allLearningReport, state.audit, state.deepseekRun] = await Promise.all([
    api("/api/current-paper"),
    api("/api/six-data"),
    api("/api/current-paper/extraction"),
    api("/api/current-paper/experiment-profile"),
    api("/api/current-paper/learning-samples"),
    api("/api/learning-samples"),
    api("/api/current-paper/learning-report"),
    api("/api/learning-report"),
    api("/api/current-paper/evidence-audit"),
    api("/api/current-paper/deepseek-run"),
  ]);
  applyReviewPriorities();
  clearDirtyRows();
  state.selected = null;
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderExperimentProfile();
  renderEvidenceAudit();
  renderDeepSeekRun();
  renderTable();
  renderHistory();
  fillManualDefaults();
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

function renderPaperOptions() {
  const optionHtml = state.papers.map(paper => {
    const rows = Number(paper.six_row_count || paper.row_count || 0);
    const scan = paper.six_workflow_label || (rows ? `${rows}条数据` : "未扫描");
    return `<option value="${esc(paper.id)}">${esc(paperLabel(paper))} · ${esc(scan)}</option>`;
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
  renderPaperStatusSummary();
}

function renderPaperStatusSummary() {
  const el = document.querySelector("#paper-status-summary");
  if (!el) return;
  const total = state.papers.length;
  const scanned = state.papers.filter(p => (Number(p.six_row_count || 0) > 0) || Number(p.completed_ai_run_count || 0) > 0).length;
  const pending = state.papers.filter(p => p.six_workflow_state === "pending_review").length;
  const reviewed = state.papers.filter(p => p.six_workflow_state === "reviewed").length;
  el.textContent = `文章处理总览：共 ${total} 篇；已扫描 ${scanned} 篇；待人工审核 ${pending} 篇；已完成 ${reviewed} 篇。下拉框中的状态来自本地数据库。`;
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
  setText("paper-compact-status", `${paperStatus.six_workflow_label || "未扫描"} · ${state.rows.length} 条数据`);
  setText("focus-paper-title", paper.title || "当前文章");
  setText("mini-title", paper.title);
  setText("mini-doi", paper.doi || "—");
  setText("nav-count", state.rows.length);
  document.querySelector("#open-paper").href = `/api/papers/${paper.id}/pdf`;
  document.querySelector("#current-export-csv").href = `/api/current-paper/export.csv?paper_id=${encodeURIComponent(paper.id)}`;
  document.querySelector("#current-export-xlsx").href = `/api/current-paper/export.xlsx?paper_id=${encodeURIComponent(paper.id)}`;
  updateReviewBatchLinks();
}

async function refreshReviewFeedback() {
  const currentPaperId = state.paper?.id;
  const requests = [
    api("/api/papers"),
    api("/api/learning-samples"),
    api("/api/learning-report"),
  ];
  if (currentPaperId) {
    requests.push(
      api(`/api/current-paper/learning-samples?paper_id=${encodeURIComponent(currentPaperId)}`),
      api(`/api/current-paper/learning-report?paper_id=${encodeURIComponent(currentPaperId)}`),
      api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(currentPaperId)}`),
      api(`/api/current-paper/extraction?paper_id=${encodeURIComponent(currentPaperId)}`),
    );
  }
  const [papers, allLearning, allLearningReport, learning, learningReport, audit, extraction] = await Promise.all(requests);
  state.papers = papers;
  state.allLearning = allLearning;
  state.allLearningReport = allLearningReport;
  if (currentPaperId) {
    state.learning = learning;
    state.learningReport = learningReport;
    state.audit = audit;
    state.extraction = extraction;
    applyReviewPriorities();
  }
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderEvidenceAudit();
  renderHistory();
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
  el.innerHTML = `最近一次 DeepSeek 运行：${esc(run.status)} · 候选 ${esc(run.candidate_count)} 条 · 双重验证通过 ${esc(run.verified_count)} 条 · 重复 ${esc(run.duplicate_count || 0)} 条 · 拒绝/歧义 ${esc(run.rejected_count)} 条 · 预览结果不会自动替代校对表${link}`;
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
  const all = document.querySelector("#current-review-all");
  if (batch) batch.href = `/api/current-paper/review-batch.md?paper_id=${encoded}&limit=20`;
  if (all) {
    const limit = Math.max(unreviewed, 1);
    all.href = `/api/current-paper/review-batch.md?paper_id=${encoded}&limit=${encodeURIComponent(limit)}`;
    all.textContent = unreviewed ? `下载全部待审核（${unreviewed}）` : "下载全部待审核";
    all.classList.toggle("disabled", unreviewed === 0);
    all.setAttribute("aria-disabled", unreviewed === 0 ? "true" : "false");
  }
}

function autoSizeReviewCell(input) {
  input.style.height = "auto";
  const target = Math.min(Math.max(input.scrollHeight + 2, 46), 132);
  input.style.height = `${target}px`;
  input.style.overflowY = input.scrollHeight > target ? "auto" : "hidden";
}

function renderTable() {
  const rows = filteredRows();
  const progress = reviewProgress();
  renderReviewProgressCard(progress);
  updateReviewBatchLinks();
  const filterNote = state.reviewFilter === "all" ? "" : ` · 当前筛出 ${rows.length} 条`;
  const sortNote = state.reviewSort === "original" ? "" : ` · ${document.querySelector("#review-sort")?.selectedOptions?.[0]?.textContent || "已排序"}`;
  const dirtyNote = hasUnsavedEdits() ? `，${state.dirtyRows.size} 行未确认` : "";
  const breakdown = `确认 ${progress.confirmed}、修正 ${progress.corrected}、歧义 ${progress.ambiguous}、不采用 ${progress.rejected}、人工 ${progress.manual}`;
  setText("row-count", `${progress.reviewed}/${progress.total} 已审核（${breakdown}），${progress.unreviewed} 待审核${dirtyNote}${filterNote}${sortNote}`);
  const body = document.querySelector("#edit-rows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty-table">当前筛选条件下没有数据。你可以切回“全部”或“只看未审核”。</div></td></tr>';
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
        : ["rejected", "ambiguous"].includes(row.review_action)
          ? `<button type="button" class="reopen-action" data-reopen="${row.item_id}">恢复待审核</button>`
          : "";
    return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action">${priorityBadge}${reviewButtons}${decisionButtons}<button data-original="${row.item_id}">查看原始</button><button class="source-action" data-source-row="${row.item_id}"${sourceDisabled}>原文证据</button><small>#${row.item_id} · ${badge}</small></div></td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-item]").forEach(tr => tr.addEventListener("click", event => {
    if (event.target.closest("button[data-confirm],button[data-confirm-next],button[data-source-row]")) return;
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
      const dirty = fields.some(field => String(collectRowFields(id)[field] ?? "") !== String(row[field] ?? ""));
      markRowDirty(id, dirty);
    });
  });
  renderOriginal(rows.find(row => Number(row.item_id) === Number(state.selected)) || null);
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

async function confirmRow(id, options = {}) {
  if (rejectReadOnlyAction("确认或修正数据")) return;
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
  const reason = document.querySelector("#review-decision-reason").value;
  const note = document.querySelector("#review-decision-note").value.trim();
  if (!reason) {
    toast("请选择主要原因。", true);
    return;
  }
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
  }
}

async function reopenReviewDecision(itemId) {
  if (rejectReadOnlyAction("恢复待审核")) return;
  try {
    const result = await api(`/api/six-data/${itemId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: "automatic", note: "用户恢复为待审核", editor: "本地研究者" }),
    });
    state.rows = state.rows.map(row => row.item_id === itemId ? result : row);
    await refreshReviewFeedback();
    state.selected = itemId;
    renderTable();
    toast("已恢复为待审核。原始抽取版本和审核历史均已保留。");
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

async function runSearch(event) {
  event?.preventDefault();
  const q = document.querySelector("#search-query").value.trim();
  state.search = q;
  try {
    if (state.searchMode === "item") {
      const rows = await api(`/api/six-search?q=${encodeURIComponent(q)}`);
      setText("search-summary", q ? `“${q}” 找到 ${rows.length} 条可用数据` : `显示 ${rows.length} 条可用数据`);
      document.querySelector("#search-export").href = `/api/six-export.csv?q=${encodeURIComponent(q)}`;
      document.querySelector("#search-export-xlsx").href = `/api/six-export.xlsx?q=${encodeURIComponent(q)}`;
      renderResults(rows);
      return;
    }
    const assets = await api(`/api/visual-search?type=${encodeURIComponent(state.searchMode)}&q=${encodeURIComponent(q)}`);
    const label = state.searchMode === "table" ? "张完整表格" : "幅完整图片";
    setText("search-summary", q ? `“${q}” 找到 ${assets.length} ${label}` : `显示 ${assets.length} ${label}`);
    renderVisualResults(assets);
  } catch (error) {
    toast(error.message, true);
  }
}

const searchModeCopy = {
  item: {
    placeholder: "例如：CoCrFeMnNi 300°C 辐照后 硬度",
    help: "检索整个六列数据库；同一原表的命中数据默认集中显示，并支持展开全部。",
  },
  table: {
    placeholder: "例如：三种材料 辐照前后 纳米硬度",
    help: "以完整表格为单位检索物理量、材料、实验条件、方法、表题和正文解释。",
  },
  figure: {
    placeholder: "例如：位错环密度 随剂量变化 300°C",
    help: "以完整图片为单位检索坐标变量、材料、条件、图注和正文结论；不会自动猜读曲线点。",
  },
};

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
  document.querySelector("#search-exports").hidden = mode !== "item";
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

function itemResultHtml(row) {
  const authors = [row.first_author ? `一作：${row.first_author}` : "", row.corresponding_author ? `通讯：${row.corresponding_author}` : ""].filter(Boolean).join(" · ");
  const paperLine = [row.article_title, row.doi, authors].filter(Boolean).join(" · ");
  const reviewLabel = searchReviewLabel(row);
  const reviewButton = isReadOnly() ? "" : `<button class="row-link" data-jump="${row.item_id}">去校对</button>`;
  const sourceButton = `<button class="row-link source-link" data-source-search="${row.item_id}">原文证据</button>`;
  const linked = row.primary_visual_asset || (row.visual_assets || []).find(asset => asset.asset_type === "figure");
  const visualButton = linked
    ? `<button class="row-link visual-link" data-visual-open="${linked.id}">${linked.asset_type === "table" ? "查看原始表格" : "查看相关图片"}</button>`
    : "";
  return `<article class="result" data-item-result="${row.item_id}"><div class="value">${esc(row.value_text)}<small> ${esc(row.unit)}</small><span class="source-kind ${esc(row.source_kind)}">${esc(sourceLabel(row))}</span></div><strong>${esc(row.meaning)}</strong><em class="search-review-state ${esc(row.review_action || row.origin_type)}">${esc(reviewLabel)}${row.search_score != null ? ` · 相关度 ${esc(row.search_score)}` : ""}</em><p>${esc(row.context_explanation)}</p><div class="result-paper">${esc(paperLine)}</div><div class="result-actions">${visualButton}${sourceButton}${reviewButton}</div></article>`;
}

function renderResults(rows) {
  const el = document.querySelector("#search-results");
  if (!rows.length) {
    el.innerHTML = '<div class="blank"><h3>没有找到相关数据</h3><p>尝试材料名称、环境条件、物理量或它们的组合。</p></div>';
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
    html.push(`<section class="table-result-group"><header><div><span>同一原表集中显示</span><h3>${esc(group.asset.label)} · ${group.rows.length} 条匹配数据</h3><p>${esc(group.asset.caption || "表格来源数据")}</p></div><button type="button" data-visual-open="${group.asset.id}">查看完整原表</button></header><div class="table-group-visible">${visible.map(itemResultHtml).join("")}</div>${hidden.length ? `<details><summary>展开其余 ${hidden.length} 条数据</summary><div>${hidden.map(itemResultHtml).join("")}</div></details>` : ""}</section>`);
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
    el.innerHTML = `<div class="blank"><h3>没有找到相关${state.searchMode === "table" ? "表格" : "图片"}</h3><p>尝试物理量、材料、实验条件、测试方法或变量关系。</p></div>`;
    return;
  }
  el.innerHTML = assets.map(asset => {
    const typeLabel = asset.asset_type === "table" ? "完整表格" : "完整图片";
    const quantities = (asset.physical_quantities || []).map(value => `<span>${esc(value)}</span>`).join("");
    const materials = (asset.materials || []).join(" · ");
    return `<article class="visual-result"><button class="visual-thumb" type="button" data-visual-open="${asset.id}" aria-label="查看${esc(asset.label)}"><img src="${esc(asset.image_url)}" alt="${esc(asset.label)}原文截图" loading="lazy"><span>${esc(typeLabel)}</span></button><div class="visual-result-copy"><div class="visual-result-title"><span>${esc(asset.label)} · PDF第 ${esc(asset.page_start)} 页</span><h3>${esc(asset.caption)}</h3></div><div class="visual-quantity-list">${quantities}</div><p>${esc(asset.context_explanation)}</p><dl><div><dt>材料</dt><dd>${esc(materials || "原文未单独列出")}</dd></div><div><dt>条件</dt><dd>${esc(asset.conditions_text || "见原文图注与正文")}</dd></div><div><dt>方法</dt><dd>${esc(asset.methods_text || "见原文")}</dd></div></dl><small>${esc(asset.article_title)} · ${esc(asset.doi)}${asset.search_score != null ? ` · 相关度 ${esc(asset.search_score)}` : ""}</small><button class="open-visual" type="button" data-visual-open="${asset.id}">查看完整${asset.asset_type === "table" ? "表格" : "图片"}</button></div></article>`;
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
    setText("visual-dialog-title", `${asset.label} · PDF第 ${asset.page_start} 页`);
    const image = document.querySelector("#visual-image");
    image.src = `${asset.image_url}?ts=${Date.now()}`;
    image.alt = `${asset.label}高分辨率原文截图`;
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
  const note = progress.unreviewed
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
});
document.querySelector("#review-sort").addEventListener("change", event => {
  state.reviewSort = event.target.value;
  state.selected = null;
  renderTable();
});
document.querySelector("#next-unreviewed").addEventListener("click", selectNextUnreviewed);
document.querySelector("#focus-review").addEventListener("click", () => setFocusReview(!state.focusReview));
document.querySelector("#exit-focus-review").addEventListener("click", () => setFocusReview(false));
document.addEventListener("keydown", handleReviewKeyboard);
document.querySelector("#search-form").addEventListener("submit", runSearch);
document.querySelectorAll("[data-search-mode]").forEach(button => button.addEventListener("click", () => setSearchMode(button.dataset.searchMode)));
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
load().catch(error => toast(error.message, true));
