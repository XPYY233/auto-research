const state = { paper: null, papers: [], searchPapers: [], searchScope: "all", selectedPaperIds: new Set(), searchPaperQuery: "", testSet: null, paperFilters: { query: "", author: "", topic: "all", status: "all", scope: "all" }, rows: [], selected: null, filter: "", reviewFilter: "all", reviewSort: "review_priority", reviewVisibleLimit: 80, reviewObject: "data", paperStage: "review", visualAssets: [], qualityRun: null, qualityCandidates: [], calibrationReviewIds: new Set(), calibrationActive: false, calibrationBatchTotal: 0, reviewNotes: new Map(), fieldDirtyRows: new Set(), search: "", searchExperience: "agent", searchMode: "item", searchFilters: { review: "all", source: "all", quality: "all", sort: "relevance" }, searchResults: [], searchRequest: 0, searchComposing: false, evidenceInspectorRequest: 0, librarianMessages: [], librarianResults: [], librarianBusy: false, librarianResultType: "item", librarianSessions: [], librarianSessionId: null, librarianHistoryQuery: "", librarianMeta: {}, librarianResearchContexts: new Map(), librarianBriefAuth: null, librarianProgressTimer: null, librarianProgressStarted: 0, visualAsset: null, detailItem: null, extraction: null, experimentProfile: null, audit: null, deepseekRun: null, uploads: [], jobs: [], ai: null, aiCatalog: null, aiPublicState: null, uiMode: { read_only: false }, runtimeWarnings: new Map(), dirtyRows: new Set(), progressTimer: null, progressValue: 0, focusReview: false, reviewDecision: null };
let librarianPendingSynthesis = null;
let librarianStageGeneration = 0;
let literatureWorkflowGeneration = 0;
const contextChat = { entity: null, conversations: new Map(), busy: false };
const defaultContextQuestion = "说明这个数据本身的含义，并总结该数据在文章中的具体含义";
const fields = ["value_text", "meaning", "unit", "article_title", "doi", "context_explanation"];
const viewCopy = {
  paper: { kicker: "LITERATURE WORKFLOW", title: "文献处理", subtitle: "导入真实 PDF，在同一流程内完成去重、自动提取和必要的证据检查。" },
  review: { kicker: "EVIDENCE CHECK", title: "检查自动提取结果", subtitle: "自动质量门决定是否收录；本页用于检查证据和修正少量异常。" },
  search: { kicker: "EXPERIMENTAL EVIDENCE LIBRARY", title: "实验文献证据检索平台", subtitle: "数据、图表、结论与 PDF 原文证据的统一检索入口。" },
  upload: { kicker: "PDF INTAKE", title: "导入实验文献", subtitle: "验证真实 PDF、识别重复论文，并加入待处理队列。" },
  personal: { kicker: "EXPERIMENT DATA INTAKE", title: "上传实验数据", subtitle: "受信 AI 提供商可预填 CSV、TSV 或 XLSX；你只需浏览、修正错误并一次确认导入。" },
  package: { kicker: "PACKAGE CENTER", title: "资料包中心", subtitle: "管理官方资料库，导出或导入课题组内部论文集合与私人实验资料包。" },
  settings: { kicker: "WORKBENCH SETTINGS", title: "设置", subtitle: "管理当前设备的外观、语言、模型服务和数据入口。" },
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
const librarianHistoryStorageKey = "evidence-librarian-history-v1";
const librarianDesktopHistoryEndpoint = "/api/desktop/librarian-history";
const librarianDesktopStorageLabels = new Set([
  'macos-keychain-aes-256-gcm',
  'macos-preview-local-key-aes-256-gcm',
]);
const librarianHistoryLimit = 16;
const librarianHistoryByteLimit = 2_500_000;
let librarianHistoryBackend = "unknown";
let librarianHistorySaveChain = Promise.resolve();
const librarianResultTypes = ["item", "finding", "table", "figure"];
const librarianMatchOrder = { direct: 0, adjacent: 1, expansion: 2 };
const reviewPageSize = 80;
const desktopCsrfHeader = "X-Auto-Research-CSRF";
let desktopCsrfToken = "";
const AI_ACTION_SCOPES = new Set(["librarian", "selected_evidence_chat", "literature_extraction", "personal_suggestion"]);
const AI_ACTION_CALL_LIMITS = Object.freeze({ librarian: 8, selected_evidence_chat: 1, literature_extraction: 512, personal_suggestion: 1 });

function aiPreparedActionRoutes(scope) {
  if (!AI_ACTION_SCOPES.has(scope)) return null;
  const root = `/api/desktop/ai/actions/${scope}`;
  return Object.freeze({ prepare: `${root}/prepare`, execute: `${root}/execute` });
}

const DESKTOP_API_ROUTES = Object.freeze({
  settings: "/api/desktop/settings",
  preferences: "/api/desktop/settings/preferences",
  aiProviders: "/api/desktop/ai/providers",
  aiSettings: "/api/desktop/ai/settings",
  aiCredential: providerId => `/api/desktop/ai/credentials/${encodeURIComponent(providerId)}`,
  aiConsent: "/api/desktop/ai/consents",
  aiPreparedActions: aiPreparedActionRoutes,
});

function desktopRequestOptions(options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && desktopCsrfToken) {
    headers.set(desktopCsrfHeader, desktopCsrfToken);
  }
  return { ...options, headers };
}

function captureDesktopCsrf(response) {
  const token = response.headers.get(desktopCsrfHeader);
  if (token) desktopCsrfToken = token;
}

async function loadDesktopSettings() {
  return api(DESKTOP_API_ROUTES.settings);
}

async function patchDesktopPreferences(preferences, expectedRevision) {
  try {
    return await api(DESKTOP_API_ROUTES.preferences, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: expectedRevision, preferences }),
    });
  } catch (error) {
    if (error.code === "settings_revision_conflict") {
      error.latest = await loadDesktopSettings();
    }
    throw error;
  }
}

async function loadAIPublicState() {
  const [catalog, settings] = await Promise.all([
    api(DESKTOP_API_ROUTES.aiProviders),
    api(DESKTOP_API_ROUTES.aiSettings),
  ]);
  if (catalog?.schema_version !== "ai-desktop-catalog-v1" || !Array.isArray(catalog.providers)) throw new Error("AI 提供商目录不可用。");
  if (settings?.schema_version !== "ai-runtime-public-state-v1") throw new Error("AI 运行设置不可用。");
  const profile = catalog.providers.find(provider => provider.provider_id === settings.provider_id);
  if (!profile) throw new Error("当前 AI 提供商不在受信目录中。");
  globalThis.AutoResearchAIConsent?.updateTrustedProviders?.(catalog.providers);
  state.aiCatalog = catalog;
  state.aiPublicState = { ...settings, provider_label: profile.display_name };
  return { catalog, settings: state.aiPublicState };
}

async function patchAISettings(providerId, taskModels, expectedRevision) {
  return api(DESKTOP_API_ROUTES.aiSettings, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_id: providerId, task_models: taskModels, expected_revision: expectedRevision }),
  });
}

async function saveAICredential(providerId, apiKey) {
  return api(DESKTOP_API_ROUTES.aiCredential(providerId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
}

async function deleteAICredential(providerId) {
  return api(DESKTOP_API_ROUTES.aiCredential(providerId), { method: "DELETE" });
}

function currentAIConsentContext(scope) {
  const runtime = state.aiPublicState;
  const disclosureVersion = globalThis.AutoResearchAIConsent?.disclosureVersions?.[scope];
  if (!runtime?.provider_id || !runtime?.provider_label || !disclosureVersion) return null;
  return { provider_id: runtime.provider_id, label: runtime.provider_label, disclosure_version: disclosureVersion };
}

function aiProviderLabel() {
  return state.aiPublicState?.provider_label || "AI 提供商";
}

async function authorizePreparedAIAction(domain, scope, domainRequest, confirmationDetail = "") {
  if (domain !== scope || !AI_ACTION_SCOPES.has(scope)) {
    const error = new Error("该 AI 功能未受支持。");
    error.code = "ai_action_scope_invalid";
    throw error;
  }
  const prepared = await globalThis.AutoResearchDesktopPorts.prepareAIAction(domain, domainRequest);
  if (scope === "librarian" && prepared?.librarian_core_version === "librarian-v3") {
    return { local_result: prepared };
  }
  const context = currentAIConsentContext(scope);
  if (!context || typeof globalThis.AutoResearchAIConsent?.ensure !== "function") {
    const error = new Error("受信 AI 提供商状态尚未就绪，请稍后重试。");
    error.code = "ai_public_state_unavailable";
    throw error;
  }
  if (!prepared || prepared.schema_version !== "server-prepared-ai-action-v1" || typeof prepared.action_id !== "string" || !prepared.action_id || prepared.scope !== scope || prepared.provider_id !== context.provider_id || prepared.disclosure_version !== context.disclosure_version) {
    throw new Error("AI 操作准备结果无效，请重试。");
  }
  // The versioned disclosure cache only records that the user has read the
  // data boundary. Every billable prepared action is confirmed separately.
  if (globalThis.AutoResearchAIConsent.ensure(scope, context) !== true) return null;
  const calls = Number(prepared.maximum_calls);
  const callLimit = AI_ACTION_CALL_LIMITS[scope];
  if (!Number.isInteger(calls) || calls < 1 || calls > callLimit) throw new Error("AI 操作调用预算无效，请重试。");
  const actionLabel = String(prepared.display || "AI 请求").slice(0, 80);
  const modelLabel = Array.isArray(prepared.model)
    ? prepared.model.join("、")
    : String(prepared.model || "当前已选模型");
  const detail = typeof confirmationDetail === "string" && confirmationDetail ? `${confirmationDetail}\n\n` : "";
  if (typeof globalThis.confirm !== "function" || !globalThis.confirm(`${actionLabel}\n\n${detail}将使用 ${context.label} 的 ${modelLabel}，本次最多调用 ${calls} 次，可能产生 API 费用。\n\n是否授权本次操作？`)) return null;
  const issued = await globalThis.AutoResearchDesktopPorts.issuePreparedConsent(prepared.action_id);
  if (issued?.schema_version !== "ai-consent-v1" || issued.scope !== scope || issued.provider_id !== context.provider_id || issued.disclosure_version !== context.disclosure_version || typeof issued.nonce !== "string" || !issued.nonce) {
    throw new Error("AI 知情同意凭证无效，请重试。");
  }
  return { action_id: prepared.action_id, consent_nonce: issued.nonce };
}

function invalidAIActionRequest() {
  const error = new Error("AI 操作请求无效。");
  error.code = "ai_action_request_invalid";
  return error;
}

async function prepareAIAction(scope, domainRequest) {
  const routes = DESKTOP_API_ROUTES.aiPreparedActions(scope);
  if (!routes || !domainRequest || typeof domainRequest !== "object" || Array.isArray(domainRequest)) throw invalidAIActionRequest();
  return api(routes.prepare, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(domainRequest),
  });
}

async function issuePreparedConsent(actionId) {
  if (typeof actionId !== "string" || !actionId || actionId.length > 4096) throw invalidAIActionRequest();
  return api(DESKTOP_API_ROUTES.aiConsent, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_id: actionId }),
  });
}

async function executePreparedAIAction(scope, actionId, consentNonce) {
  const routes = DESKTOP_API_ROUTES.aiPreparedActions(scope);
  if (!routes || typeof actionId !== "string" || !actionId || actionId.length > 4096 || typeof consentNonce !== "string" || !consentNonce || consentNonce.length > 4096) throw invalidAIActionRequest();
  return api(routes.execute, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_id: actionId, consent_nonce: consentNonce }),
  });
}

globalThis.AutoResearchDesktopPorts = Object.freeze({
  loadDesktopSettings,
  patchDesktopPreferences,
  loadAIPublicState,
  patchAISettings,
  saveAICredential,
  deleteAICredential,
  prepareAIAction,
  issuePreparedConsent,
  executePreparedAIAction,
  currentAIConsentContext,
  authorizePreparedAIAction,
  invalidateEvidenceInspector,
});

async function api(url, options = {}) {
  const response = await fetch(url, desktopRequestOptions(options));
  captureDesktopCsrf(response);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || body.message || `请求失败 ${response.status}`);
    error.code = typeof body.code === 'string' ? body.code : '';
    throw error;
  }
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
  document.querySelectorAll('[data-view="paper"],[data-view="upload"],[data-view="personal"],[data-view="package"],[data-write-action]').forEach(el => {
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
  if (searchSummary) searchSummary.textContent = "只读模式：支持搜索、条目级受信 AI 解读、原文证据和导出";
}

function renderViewHeader(name) {
  const copy = viewCopy[name] || viewCopy.review;
  setText("workspace-kicker", copy.kicker);
  setText("workspace-title", copy.title);
  setText("workspace-subtitle", copy.subtitle);
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

function paperAutomaticStatus(paper) {
  const count = Number(paper?.six_row_count || 0);
  if (count) return `已自动收录 ${count} 条`;
  if (Number(paper?.completed_ai_run_count || 0) > 0) return "已扫描，无可报告数据";
  return "未扫描";
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
  state.searchPapers = await apiOptional("/api/search-papers", [], "检索文章目录");
  renderSearchScope();
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
  await loadCurrentPaper();
  renderPaperOptions();
  renderUploadWorkspace();
}

async function loadCurrentPaper() {
  setLiteratureInspector(false);
  [state.paper, state.rows, state.visualAssets, state.extraction, state.experimentProfile, state.deepseekRun, state.qualityRun, state.qualityCandidates] = await Promise.all([
    api("/api/current-paper"),
    api("/api/six-data"),
    apiOptional("/api/current-paper/visual-assets", [], "当前文章图表证据"),
    apiOptional("/api/current-paper/extraction", null, "当前文章处理状态"),
    apiOptional("/api/current-paper/experiment-profile", null, "实验类型识别"),
    apiOptional("/api/current-paper/deepseek-run", null, "DeepSeek 运行记录"),
    apiOptional("/api/current-paper/quality-run", null, "自动质量门状态"),
    apiOptional("/api/current-paper/quality-candidates?status=manual_review", [], "自动拦截队列"),
  ]);
  state.audit = null;
  state.reviewVisibleLimit = reviewPageSize;
  state.calibrationActive = false;
  state.reviewObject = state.qualityCandidates.length ? "quality" : "data";
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
  renderQualityStatus();
  renderReviewObject();
  renderTable();
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
    const scan = paperAutomaticStatus(paper);
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

function renderPaperLibraryList(papers) {
  const list = document.querySelector("#paper-library-list");
  if (!list) return;
  if (!papers.length) {
    list.innerHTML = '<p class="paper-library-empty">没有匹配论文。请调整题目、作者、方向或处理状态筛选。</p>';
    return;
  }
  const hasCurrentPaper = papers.some(paper => String(paper.id) === String(state.paper?.id));
  list.innerHTML = papers.map((paper, index) => {
    const active = String(paper.id) === String(state.paper?.id);
    const keyboardTarget = active || (!hasCurrentPaper && index === 0);
    const authors = [paper.first_author || "作者待补", paper.year || "年份待补"].join(" · ");
    const status = paperAutomaticStatus(paper);
    return `<button type="button" role="option" aria-selected="${active ? "true" : "false"}" tabindex="${keyboardTarget ? "0" : "-1"}" data-paper-library="${esc(paper.id)}"><strong class="wb-literature-title">${esc(paper.title || "未命名论文")}</strong><span class="wb-literature-meta">${esc(authors)}${paper.doi ? ` · ${esc(paper.doi)}` : ""}</span><small>${esc(status)}</small></button>`;
  }).join("");
  list.querySelectorAll("[data-paper-library]").forEach(button => {
    button.addEventListener("click", async () => {
      const paperId = Number(button.dataset.paperLibrary);
      const select = document.querySelector("#paper-switch-input");
      if (select) select.value = String(paperId);
      renderPaperSelectionMeta();
      try {
        const switched = await switchCurrentPaper({ paperId, silent: true });
        if (!switched) return;
        rememberRecentPaper(paperId);
        renderPaperOptions();
        toast(`已切换到《${state.paper.title || "未命名文章"}》。`);
      } catch (error) {
        toast(error.message, true);
      }
    });
    button.addEventListener("keydown", event => handlePaperLibraryKeydown(event));
  });
}

function handlePaperLibraryKeydown(event) {
  const buttons = [...document.querySelectorAll("[data-paper-library]")];
  const current = buttons.indexOf(event.currentTarget);
  const direction = { ArrowDown: 1, ArrowUp: -1, Home: "home", End: "end" }[event.key];
  if (current < 0 || direction === undefined) return;
  event.preventDefault();
  const next = direction === "home" ? buttons[0]
    : direction === "end" ? buttons[buttons.length - 1]
    : buttons[(current + direction + buttons.length) % buttons.length];
  buttons.forEach(button => { button.tabIndex = button === next ? 0 : -1; });
  next?.focus();
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
  const submit = document.querySelector("#paper-switch-submit");
  if (submit) submit.disabled = !filteredPapers.length;
  const summary = document.querySelector("#paper-filter-summary");
  if (summary) summary.textContent = `找到 ${filteredPapers.length} / ${state.papers.length} 篇`;
  renderPaperLibraryList(filteredPapers);
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
  const testSetCount = Number(state.testSet?.paper_count || 0);
  const collected = state.papers.reduce((sum, paper) => sum + Number(paper.six_row_count || 0), 0);
  el.textContent = `文章处理总览：共 ${total} 篇；全库测试集 ${testSetCount} 篇；已扫描 ${scanned} 篇；自动收录 ${collected} 个独立事实。`;
  el.className = scanned ? "supported" : "";
}

function renderPaper() {
  const paper = state.paper;
  if (!paper) return;
  const paperStatus = state.papers.find(p => String(p.id) === String(paper.id)) || paper;
  const authorYear = [paper.first_author || "作者待补", paper.year || "年份待补"].filter(Boolean).join(" · ");
  setText("paper-key", authorYear);
  setText("paper-title", paper.title);
  setText("paper-doi", paper.doi || "—");
  setText("paper-process-status", paperAutomaticStatus(paperStatus));
  setText("paper-compact-status", `${paperAutomaticStatus(paperStatus)} · ${state.rows.length} 个独立事实`);
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
  const commonRequests = [api("/api/papers")];
  const paperRequests = [];
  if (currentPaperId) {
    paperRequests.push(
      api(`/api/current-paper/extraction?paper_id=${encodeURIComponent(currentPaperId)}`),
    );
    if (includeAudit) {
      paperRequests.push(api(`/api/current-paper/evidence-audit?paper_id=${encodeURIComponent(currentPaperId)}`));
    }
  }
  const [[papers], paperPayload] = await Promise.all([
    Promise.all(commonRequests),
    Promise.all(paperRequests),
  ]);
  state.papers = papers;
  if (currentPaperId) {
    state.extraction = paperPayload[0];
    if (includeAudit) state.audit = paperPayload[1];
    applyReviewPriorities();
  }
  renderPaperOptions();
  renderPaper();
  renderExtractionStatus();
  renderEvidenceAudit();
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
  button.textContent = status.action === "prepare_packet" ? "生成抽取包" : "一键提取并核验";
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
  const modeConfidence = Math.round((profile.mode_confidence || profile.confidence || 0) * 100);
  const mode = profile.paper_mode || (profile.is_experimental ? "experimental" : "unknown");
  card.classList.toggle("warn", mode === "unknown");
  if (mode === "experimental" || mode === "mixed_experiment_computation") {
    summary.textContent = `研究模式：${profile.paper_mode_label || "实验研究"}；主类型：${profile.primary_label}；识别置信度 ${modeConfidence}%。系统将按该边界选择抽取重点。`;
  } else if (mode === "computational_modeling" || mode === "review_report") {
    summary.textContent = `研究模式：${profile.paper_mode_label}；识别置信度 ${modeConfidence}%。系统会限制证据类型，避免将计算量或引文数据标成直接测量。`;
  } else {
    summary.textContent = "研究类型尚不明确；自动抽取将保持保守并提示人工检查。";
  }
  const typeTags = (profile.types || []).slice(0, 5).map((item, index) => (
    `<span class="${index === 0 ? "primary" : ""}">${esc(item.label)} · ${esc(item.score)}</span>`
  ));
  tags.innerHTML = typeTags.length
    ? typeTags.join("")
    : `<span>${esc(profile.paper_mode_label || "未识别到稳定研究类型")}</span>`;
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
  if (!el) return;
  if (!state.ai?.configured) {
    el.textContent = "DeepSeek 未配置：已保存数据仍可浏览、检索和修正，但新文章无法运行自动质量提取。";
    el.className = "unsupported";
    return;
  }
  if (!state.deepseekRun) {
    el.textContent = state.rows.length
      ? "当前文章已有入库数据；新提取结果必须通过双路对照或第三次复核后才进入搜索。"
      : "尚无 DeepSeek 抽取记录；系统将运行两套独立抽取并执行质量评分。";
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
    el.innerHTML = `最近一次 DeepSeek 运行在后处理阶段失败，但此前已保存的 ${state.rows.length} 条数据和${visualText.replace(/^ · /, "")}仍可检查。失败原因：${esc(run.error_message || "未记录")}${link}`;
    el.className = "unsupported";
    return;
  }
  el.innerHTML = `最近一次 DeepSeek 运行：${esc(run.status)} · 数值候选 ${esc(run.candidate_count)} 条 · 证据验证通过 ${esc(run.verified_count)} 条${findingText}${visualText} · 重复 ${esc(run.duplicate_count || 0)} 条 · 拒绝/歧义 ${esc(run.rejected_count)} 条 · 通过自动质量门的候选已进入搜索${link}`;
  el.className = run.status === "completed" ? "supported" : run.status === "failed" ? "unsupported" : "ready";
}

function qualityGateLabel(status) {
  return {
    dual_pass: "双路一致通过",
    third_pass: "第三次复核通过",
    manual_review: "自动拦截",
    manual_approved: "历史人工通过",
    rejected: "不采用",
    legacy_stable: "历史稳定数据",
  }[status] || "质量状态未记录";
}

function qualityStageLabel(stage) {
  return {
    queued: "等待开始",
    visual_index: "建立本地图表证据",
    dual_extraction: "两路 DeepSeek 并行抽取",
    adversarial_compare: "对抗式比较与评分",
    third_review: "第三次独立复核低分项",
    publish: "发布通过质量门的结果",
    completed: "质量检测完成",
    failed: "质量检测失败",
  }[stage] || stage || "尚未运行";
}

function renderQualityStatus() {
  const run = state.qualityRun;
  const stage = document.querySelector("#quality-run-stage");
  const summary = document.querySelector("#quality-run-summary");
  const strip = document.querySelector("#quality-score-strip");
  if (!stage || !summary || !strip) return;
  if (!run) {
    stage.textContent = "尚未运行";
    summary.textContent = "两路 DeepSeek 将独立抽取并互相对照；低分项再由第三路复核。";
    strip.innerHTML = "";
    return;
  }
  stage.textContent = `${qualityStageLabel(run.stage)} · ${Number(run.progress || 0)}%`;
  const counts = run.gate_counts || run.summary?.gate_counts || {};
  const passed = Number(counts.dual_pass || 0) + Number(counts.third_pass || 0) + Number(counts.manual_approved || 0);
  const manual = Number(counts.manual_review || 0);
  summary.textContent = run.status === "failed"
    ? `质量检测未完成：${run.error_message || "未记录原因"}`
    : run.status === "running"
      ? (run.summary?.branch_progress
        ? `完整性分支 ${Math.round(Number(run.summary.branch_progress.completeness || 0))}% · 精确性分支 ${Math.round(Number(run.summary.branch_progress.precision || 0))}%`
        : "系统正在并行运行两套独立抽取，并将低分项送入第三次复核。")
      : `共评估 ${run.summary?.candidate_count || passed + manual} 项；已发布 ${passed} 项，自动拦截 ${manual} 项。`;
  strip.innerHTML = [
    ["双路通过", counts.dual_pass || 0, "pass"],
    ["第三次通过", counts.third_pass || 0, "third"],
    ["自动拦截", counts.manual_review || 0, "manual"],
    ["不采用", counts.rejected || 0, "reject"],
  ].map(([label, value, kind]) => `<span class="${kind}"><b>${esc(value)}</b>${label}</span>`).join("");
}

async function pollQualityProgress(paperId) {
  clearInterval(state.progressTimer);
  return new Promise(resolve => {
    const poll = async () => {
      try {
        const run = await api(`/api/current-paper/quality-run?paper_id=${encodeURIComponent(paperId)}`);
        if (!run) return;
        state.qualityRun = run;
        renderQualityStatus();
        updateProgress(Number(run.progress || 0), `自动质量门：${qualityStageLabel(run.stage)}`, run.status === "running");
        if (run.status !== "running") {
          clearInterval(state.progressTimer);
          state.progressTimer = null;
          resolve(run);
        }
      } catch (_error) {
        // The extraction request remains authoritative; a transient poll failure is harmless.
      }
    };
    state.progressTimer = setInterval(poll, 1800);
    void poll();
  });
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
  const correctionNote = progress.reviewed ? ` · 历史检查/修正 ${progress.reviewed} 条` : "";
  setText("row-count", `已自动收录 ${progress.total} 个物理事实${correctionNote}${dirtyNote}${filterNote}${visibleNote}${sortNote}`);
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
    const badge = row.origin_type === "manual" ? "人工补录" : row.review_action === "confirmation" ? `历史确认 v${row.version_no}` : row.review_action === "correction" ? `历史修正 v${row.version_no}` : row.review_action === "rejected" ? `已排除 v${row.version_no}` : row.review_action === "ambiguous" ? `证据歧义 v${row.version_no}` : "自动收录";
    const sourceDisabled = row.origin_type === "manual" ? " disabled" : "";
    const reviewButtons = isReadOnly()
      ? ""
      : `<button class="confirm" data-confirm="${row.item_id}">保存当前修正</button><button class="confirm-next" data-confirm-next="${row.item_id}">保存并下一条</button>`;
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
    return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action">${clusterNote}${priorityBadge}${qualityBadge(row)}${reviewButtons}${decisionButtons}<button data-original="${row.item_id}">查看原始</button>${evidenceMenu}<small>#${row.item_id} · ${badge}</small></div></td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-item]").forEach(tr => tr.addEventListener("click", event => {
    if (event.target.closest("button[data-confirm],button[data-confirm-next],button[data-source-row]")) return;
    selectRow(Number(tr.dataset.item), { revealInspector: true });
  }));
  body.querySelectorAll("[data-original]").forEach(btn => btn.addEventListener("click", event => {
    event.stopPropagation();
    selectRow(Number(btn.dataset.original), { revealInspector: true });
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
    selectRow(Number(btn.dataset.sourceRow), { revealInspector: true });
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
    automatic: "自动元数据",
    confirmation: `历史确认 v${asset.version_no}`,
    correction: `历史修正 v${asset.version_no}`,
    ambiguous: `存在歧义 v${asset.version_no}`,
    rejected: `不采用 v${asset.version_no}`,
  }[asset.review_action] || "自动元数据";
}

function setReviewObject(mode) {
  if (!["data", "table", "figure", "quality"].includes(mode)) return;
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
  setText("review-quality-count", `${state.qualityCandidates.length} 项`);
  document.querySelectorAll("[data-review-object]").forEach(button => {
    const active = button.dataset.reviewObject === state.reviewObject;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  const dataMode = state.reviewObject === "data";
  const qualityMode = state.reviewObject === "quality";
  document.querySelector("#data-review-workspace").hidden = !dataMode;
  document.querySelector("#visual-review-workspace").hidden = dataMode || qualityMode;
  document.querySelector("#quality-review-workspace").hidden = !qualityMode;
  const controls = document.querySelector("#view-review .view-actions");
  if (controls) controls.hidden = !dataMode;
  if (qualityMode) renderQualityReview();
  else if (!dataMode) renderVisualReview();
}

function qualityReviewCard(record) {
  const candidate = record.candidate || {};
  const page = candidate.source_page || candidate.page_start;
  const typeLabel = { data: "数据", finding: "实验结论", table: "表格", figure: "图片" }[record.entity_type] || record.entity_type;
  const title = candidate.meaning || candidate.display_name || candidate.label || candidate.finding_text || "自动拦截候选";
  const sourceAction = ["table", "figure"].includes(record.entity_type)
    ? `<button type="button" data-quality-visual="${esc(candidate.asset_id)}">查看原始截图</button>`
    : `<a href="/api/papers/${esc(state.paper?.id)}/pdf#page=${esc(page || 1)}" target="_blank" rel="noopener">打开原文页</a>`;
  return `<article class="quality-review-card" data-quality-candidate="${record.id}">
    <header><div><span>${esc(typeLabel)} · ${esc(record.candidate_key)}</span><h3>${esc(title)}</h3></div><strong>${esc(record.overall_score)}<small>/100</small></strong></header>
    <div class="quality-dimensions"><span>一致性 <b>${esc(record.agreement_score)}</b></span><span>事实性 <b>${esc(record.factuality_score)}</b></span><span>完整度 <b>${esc(record.completeness_score)}</b></span><span>证据 <b>${esc(record.evidence_score)}</b></span></div>
    <p class="quality-reason">${esc(record.gate_reason)}</p>
    ${candidate.source_excerpt ? `<blockquote><span>原文证据 · PDF 第 ${esc(page)} 页</span>${esc(brief(candidate.source_excerpt, 420))}</blockquote>` : ""}
    <div class="quality-quarantine-note"><strong>未进入搜索</strong><span>第三次自动复核后仍低于阈值。需要更新时，请重新运行本篇自动提取，由质量门重新评估。</span></div>
    <div class="quality-review-actions">${sourceAction}</div>
  </article>`;
}

function renderQualityReview() {
  const grid = document.querySelector("#quality-review-grid");
  if (!grid) return;
  if (!state.qualityCandidates.length) {
    grid.innerHTML = `<div class="blank"><span>✓</span><h3>没有被自动拦截的候选</h3><p>双路一致或第三次复核通过的结果已自动发布；未运行时可点击“一键提取并核验”。</p></div>`;
    return;
  }
  grid.innerHTML = state.qualityCandidates.map(qualityReviewCard).join("");
  grid.querySelectorAll("[data-quality-visual]").forEach(button => button.addEventListener("click", () => openVisualAsset(Number(button.dataset.qualityVisual))));
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
    ${asset.review_action !== "automatic" ? '<button type="button" data-visual-decision="automatic">恢复自动版本</button>' : ""}
  </div>`;
  return `<article class="visual-review-card ${statusClass}" data-visual-review="${asset.id}">
    <div class="visual-review-proof">
      <div class="visual-review-proof-head"><span>${esc(asset.label)} · PDF 第 ${esc(asset.page_start)} 页</span><em>${esc(visualReviewLabel(asset))}</em></div>
      <button type="button" class="visual-review-image" data-open-review-visual="${asset.id}" aria-label="放大查看${esc(asset.label)}"><img src="${esc(asset.image_url)}" alt="${esc(asset.label)}原文截图" loading="lazy"></button>
      <div class="visual-proof-actions"><button type="button" data-open-review-visual="${asset.id}">放大查看</button><a href="${esc(asset.pdf_url)}" target="_blank" rel="noopener">打开原文 PDF</a></div>
      <p><strong>原始图注</strong>${esc(asset.original_caption || asset.caption)}</p>
    </div>
    <form class="visual-review-form">
      <header><div><small>${typeName.toUpperCase()} METADATA</small><h3 class="visual-review-rich-title">${visualTitleHtml(asset, { highlight: false })}</h3></div><span>${esc(asset.label)} · 截图与原图注保持不变</span></header>
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
  const typeName = type === "table" ? "原始表格" : "论文图片";
  setText("visual-review-kicker", type === "table" ? "TABLE EVIDENCE CHECK" : "FIGURE EVIDENCE CHECK");
  setText("visual-review-title", `${typeName}检查`);
  const reviewed = assets.filter(asset => asset.review_action !== "automatic").length;
  setText("visual-review-summary", `共 ${assets.length} 项，历史修正 ${reviewed} 项。可检查截图、图注与检索标签；图片曲线不会被自动转换为精确数值。`);
  const grid = document.querySelector("#visual-review-grid");
  if (!assets.length) {
    grid.innerHTML = `<div class="blank visual-review-empty"><span>▧</span><h3>当前文章尚未建立${typeName}</h3><p>运行“一键提取并核验”后，系统会先从本地 PDF 建立高分辨率图表截图；识别不到的扫描件将保留为待处理任务。</p></div>`;
    return;
  }
  grid.innerHTML = assets.map(visualReviewCard).join("");
  grid.querySelectorAll("[data-open-review-visual]").forEach(button => button.addEventListener("click", () => openVisualAsset(Number(button.dataset.openReviewVisual))));
  grid.querySelectorAll("[data-visual-decision]").forEach(button => button.addEventListener("click", () => {
    const card = button.closest("[data-visual-review]");
    saveVisualReview(Number(card.dataset.visualReview), button.dataset.visualDecision, card.querySelector("form"));
  }));
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
    toast(decision === "automatic" ? "该图表已恢复为自动提取版本。" : `图表修正已保存：${visualReviewLabel(result)}。`);
  } catch (error) {
    toast(error.message, true);
    if (button) button.disabled = false;
  }
}

function selectRow(id, { revealInspector = false } = {}) {
  state.selected = id;
  const row = state.rows.find(item => item.item_id === id);
  renderOriginal(row);
  document.querySelectorAll("#edit-rows tr[data-item]").forEach(tr => tr.classList.toggle("selected", Number(tr.dataset.item) === id));
  if (revealInspector && !globalThis.matchMedia?.("(min-width: 1280px)")?.matches) setLiteratureInspector(true);
}

function setLiteratureInspector(open) {
  const active = Boolean(open) && document.body.dataset.view === "paper" && state.paperStage === "review";
  const workspace = document.querySelector("#view-review");
  const pane = document.querySelector("#original-pane");
  const toggle = document.querySelector("#literature-inspector-toggle");
  workspace?.classList.toggle("has-literature-inspector", active);
  if (toggle) {
    toggle.setAttribute("aria-expanded", active ? "true" : "false");
    toggle.textContent = active ? "隐藏证据检查器" : "显示证据检查器";
  }
  if (pane) pane.setAttribute("aria-hidden", active || globalThis.matchMedia?.("(min-width: 1280px)")?.matches ? "false" : "true");
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
  if (document.body.dataset.view !== "paper" || state.paperStage !== "review") return;
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

function selectedSearchPaperParam() {
  if (state.searchScope !== "selected") return "";
  return [...state.selectedPaperIds].sort((a, b) => a - b).join(",");
}

function searchPaperMatches(paper) {
  const query = state.searchPaperQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  return [paper.title, paper.doi, paper.first_author, paper.corresponding_author, paper.year]
    .filter(Boolean).join(" ").toLocaleLowerCase("zh-CN").includes(query);
}

function renderSearchScope() {
  const panel = document.querySelector("#search-paper-panel");
  if (!panel) return;
  const selectedMode = state.searchScope === "selected";
  panel.hidden = state.searchExperience !== "precise" || !selectedMode;
  document.querySelectorAll("[data-search-scope]").forEach(button => {
    const active = button.dataset.searchScope === state.searchScope;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const selectedCount = state.selectedPaperIds.size;
  setText("search-scope-heading", selectedMode ? `指定 ${selectedCount} 篇文章` : "全部文章");
  setText("search-scope-caption", selectedMode ? "所有检索模式与导出保持同一范围" : "不受当前检查文章影响");
  setText("search-paper-count", selectedMode ? `已选择 ${selectedCount} 篇` : `全库 ${state.searchPapers.length || 0} 篇文章`);
  const papers = state.searchPapers.filter(searchPaperMatches);
  const list = document.querySelector("#search-paper-list");
  list.innerHTML = papers.length ? papers.map(paper => {
    const checked = state.selectedPaperIds.has(Number(paper.id));
    const meta = [paper.first_author, paper.year, paper.doi].filter(Boolean).join(" · ");
    return `<label class="search-paper-option${checked ? " selected" : ""}"><input type="checkbox" value="${esc(paper.id)}"${checked ? " checked" : ""}><span><strong>${esc(paper.title || "未命名文章")}</strong><small>${esc(meta || "暂无 DOI/作者信息")} · ${esc(paper.six_row_count || 0)} 条数据</small></span></label>`;
  }).join("") : `<div class="search-paper-empty">没有匹配的文章</div>`;
  list.querySelectorAll('input[type="checkbox"]').forEach(input => input.addEventListener("change", () => {
    const paperId = Number(input.value);
    if (input.checked) state.selectedPaperIds.add(paperId);
    else state.selectedPaperIds.delete(paperId);
    renderSearchScope();
    runSearch(null, { remember: false });
  }));
  renderActiveFilters();
}

function setSearchScope(mode) {
  if (!["all", "selected"].includes(mode)) return;
  invalidateEvidenceInspector();
  state.searchScope = mode;
  renderSearchScope();
  if (state.searchExperience === 'precise') runSearch(null, { remember: false });
}

function itemSearchParams(q) {
  const params = new URLSearchParams({
    q,
    review: state.searchFilters.review,
    source: state.searchFilters.source,
    quality: state.searchFilters.quality,
    sort: state.searchFilters.sort,
  });
  const paperIds = selectedSearchPaperParam();
  if (paperIds) params.set("paper_ids", paperIds);
  return params.toString();
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
  invalidateEvidenceInspector();
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
  if (state.searchScope === "selected") labels.push(`指定 ${state.selectedPaperIds.size} 篇文章`);
  const sourceLabels = { text: "正文数据", table: "表格数据", figure: "图片关联", manual: "人工补录" };
  if (sourceLabels[state.searchFilters.source]) labels.push(sourceLabels[state.searchFilters.source]);
  const qualityLabels = { quality_passed: "自动质量门通过", dual_pass: "双路一致通过", third_pass: "第三次复核通过", legacy_stable: "历史稳定数据" };
  if (qualityLabels[state.searchFilters.quality]) labels.push(qualityLabels[state.searchFilters.quality]);
  const sortLabels = { source_page: "按文章与页码", article: "按文章归类" };
  if (sortLabels[state.searchFilters.sort]) labels.push(sortLabels[state.searchFilters.sort]);
  return labels;
}

function renderActiveFilters() {
  const container = document.querySelector("#search-active-filters");
  const labels = state.searchMode === "item"
    ? activeFilterLabels()
    : activeFilterLabels().filter(label => label.startsWith("指定 ") || label.includes("通过") || label === "历史稳定数据");
  container.innerHTML = labels.map(label => `<span>${esc(label)}</span>`).join("");
}

function resetSearchWorkspace() {
  state.searchFilters = { review: "all", source: "all", quality: "all", sort: "relevance" };
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-quality-filter").value = "all";
  document.querySelector("#search-sort").value = "relevance";
  document.querySelector("#search-query").value = "";
  document.querySelector("#search-clear").hidden = true;
  renderActiveFilters();
  runSearch(null, { remember: false });
}

let searchInputTimer = null;
function scheduleSearchFromInput() {
  const input = document.querySelector("#search-query");
  invalidateEvidenceInspector();
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
  if (state.searchExperience !== "precise") return;
  invalidateEvidenceInspector();
  if (globalThis.AutoResearchDesktopProduct?.handleSearch(event, options)) return;
  const q = document.querySelector("#search-query").value.trim();
  const requestId = ++state.searchRequest;
  state.search = q;
  document.querySelector("#search-clear").hidden = !q;
  if (state.searchScope === "selected" && !state.selectedPaperIds.size) {
    state.searchResults = [];
    setText("search-summary", "请先选择至少一篇文章");
    document.querySelector("#search-results").classList.remove("is-loading");
    document.querySelector("#search-results").innerHTML = `<div class="blank search-empty"><span>＋</span><h3>尚未选择检索文章</h3><p>在上方勾选一篇或多篇文章后，四种检索模式和导出会使用同一范围。</p></div>`;
    return;
  }
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
      const quality = encodeURIComponent(state.searchFilters.quality);
      const paperScope = selectedSearchPaperParam();
      const paperQuery = paperScope ? `&paper_ids=${encodeURIComponent(paperScope)}` : "";
      const result = await api(`/api/qualitative-search?q=${encodeURIComponent(q)}&quality=${quality}&limit=100${paperQuery}`);
      if (requestId !== state.searchRequest) return;
      const rows = result.rows || [];
      state.searchResults = rows;
      const countText = result.total > rows.length
        ? `显示前 ${rows.length} 条，共 ${result.total} 条`
        : `${rows.length} 条结论`;
      setText("search-summary", q ? `“${q}” · ${countText}` : `当前收录 · ${countText}`);
      document.querySelector("#search-export").href = `/api/qualitative-export.csv?q=${encodeURIComponent(q)}&quality=${quality}${paperQuery}`;
      document.querySelector("#search-export-xlsx").href = `/api/qualitative-export.xlsx?q=${encodeURIComponent(q)}&quality=${quality}${paperQuery}`;
      renderQualitativeResults(rows);
    } else {
      const paperScope = selectedSearchPaperParam();
      const paperQuery = paperScope ? `&paper_ids=${encodeURIComponent(paperScope)}` : "";
      const assets = await api(`/api/visual-search?type=${encodeURIComponent(state.searchMode)}&q=${encodeURIComponent(q)}&quality=${encodeURIComponent(state.searchFilters.quality)}${paperQuery}`);
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

function setSearchExperience(mode, options = {}) {
  if (!['agent', 'precise'].includes(mode)) return;
  if (mode !== 'agent') clearLibrarianPendingSynthesis();
  invalidateEvidenceInspector();
  state.searchExperience = mode;
  document.querySelectorAll('[data-search-experience]').forEach(button => {
    const active = button.dataset.searchExperience === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelector('#librarian-workspace').hidden = mode !== 'agent';
  document.querySelector('#precise-search-workspace').hidden = mode !== 'precise';
  document.querySelector('#search-filter-bar').hidden = mode !== 'precise';
  document.querySelector('.search-toolbar').hidden = mode !== 'precise';
  const guide = document.querySelector('#search-experience-guide');
  if (guide) guide.innerHTML = mode === 'agent'
    ? '<strong>直接描述研究问题</strong><p>图书管理员默认检索完整文献库。需要限制论文范围或逐字段筛选时，再切换到精确检索。</p>'
    : '<strong>精确控制检索条件</strong><p>选择结果类型、质量来源和文章范围；该模式不调用 AI，适合快速复核和导出。</p>';
  renderSearchScope();
  if (mode === 'agent') {
    renderLibrarianResults(state.librarianResults);
    window.requestAnimationFrame(() => document.querySelector('#librarian-input')?.focus());
  } else {
    document.querySelector('#librarian-result-tabs').hidden = true;
    if (options.run !== false) runSearch(null, { remember: false });
    window.requestAnimationFrame(() => document.querySelector('#search-query')?.focus());
  }
  globalThis.AutoResearchDesktopProduct?.applySearchUI?.();
}

function moveTabFocus(button, selector, direction) {
  const tabs = [...document.querySelectorAll(selector)].filter(tab => !tab.disabled && !tab.hidden);
  const current = tabs.indexOf(button);
  if (current < 0 || !tabs.length) return;
  const next = direction === "home" ? tabs[0]
    : direction === "end" ? tabs[tabs.length - 1]
    : tabs[(current + direction + tabs.length) % tabs.length];
  next.focus();
  next.click();
}

function handleSearchTabKeydown(event, selector) {
  const direction = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1, Home: "home", End: "end" }[event.key];
  if (direction === undefined) return;
  event.preventDefault();
  moveTabFocus(event.currentTarget, selector, direction);
}

function librarianSessionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `library-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function librarianResearchContext(sessionId = state.librarianSessionId) {
  return sessionId ? state.librarianResearchContexts.get(sessionId) || null : null;
}

function clearLibrarianResearchContext(sessionId = state.librarianSessionId) {
  if (sessionId) state.librarianResearchContexts.delete(sessionId);
}

function clearLibrarianPendingSynthesis(message = '') {
  librarianStageGeneration += 1;
  librarianPendingSynthesis = null;
  const stage = document.querySelector('#librarian-synthesis-stage');
  if (stage) stage.hidden = true;
  if (message) setText('librarian-status', message);
}

function validLibrarianSynthesisStage(result) {
  if (!result || result.schema_version !== 'librarian-ai-stage-v1' || result.stage !== 'synthesis_ready' || result.requires_second_consent !== true) return false;
  if (typeof result.job_token !== 'string' || !result.job_token || result.job_token.length > 4096) return false;
  const counts = [result.candidate_count, result.bundle_count, result.review_theme_count];
  return counts.every(value => Number.isInteger(value) && value >= 0 && value <= 100000)
    && result.next_call?.maximum_calls === 1;
}

function showLibrarianSynthesisStage(result, context) {
  if (!validLibrarianSynthesisStage(result)) throw new Error('图书管理员阶段结果无效。');
  const generation = ++librarianStageGeneration;
  librarianPendingSynthesis = {
    generation,
    sessionId: state.librarianSessionId,
    jobToken: result.job_token,
    question: context.question,
    history: context.history,
    recoveryQuestion: context.recoveryQuestion,
  };
  setText('librarian-stage-candidates', String(result.candidate_count));
  setText('librarian-stage-bundles', String(result.bundle_count));
  setText('librarian-stage-themes', String(result.review_theme_count));
  const stage = document.querySelector('#librarian-synthesis-stage');
  if (stage) stage.hidden = false;
  setText('librarian-status', '检索规划已完成 · 尚未生成回答，也未进行第二次模型调用');
}

function rememberLibrarianResearchContext(result) {
  const researchState = result?.research_state;
  const stateToken = result?.state_token;
  const conversationId = String(researchState?.conversation_id || '');
  if (!researchState || typeof researchState !== 'object' || !stateToken) return;
  if (!state.librarianSessionId || conversationId !== state.librarianSessionId) {
    clearLibrarianResearchContext();
    return;
  }
  state.librarianResearchContexts.set(state.librarianSessionId, {
    conversation_id: state.librarianSessionId,
    research_state: researchState,
    state_token: stateToken,
  });
}

function librarianResearchRequest(question, history) {
  const request = {
    question,
    history,
    conversation_id: state.librarianSessionId,
  };
  const context = librarianResearchContext();
  if (context?.research_state && context?.state_token) {
    request.research_state = context.research_state;
    request.state_token = context.state_token;
  }
  return request;
}

function librarianStateFailureCode(result) {
  const code = String(result?.error?.code || '');
  return /^(research_state_|anchor_resolution_failed$|idempotent_replay_result_unavailable$)/.test(code) ? code : '';
}

const librarianTransportFailureMessage = '本次检索未完成，请稍后重试。为避免重复使用状态，旧 R# 与 B# 已停止使用。';
const librarianTransportFailureCodes = new Set([
  'librarian_request_invalid',
  'librarian_question_invalid',
  'librarian_history_invalid',
  'librarian_conversation_invalid',
  'librarian_research_state_invalid',
  'librarian_research_state_too_large',
  'librarian_state_token_invalid',
  'librarian_content_type_invalid',
  'librarian_request_too_large',
  'librarian_transport_failed',
  'rate_limited',
  'desktop_session_required',
  'desktop_media_type_required',
  'desktop_csrf_required',
]);

function librarianTransportFailureCode(error) {
  const code = String(error?.code || '');
  return librarianTransportFailureCodes.has(code) ? code : 'librarian_transport_failed';
}

function librarianSessionTitle(messages) {
  const first = messages.find(message => message.role === 'user')?.content || '新对话';
  return first.length > 34 ? `${first.slice(0, 34)}…` : first;
}

function validLibrarianSessions(value) {
  return Array.isArray(value)
    ? value.filter(session => session?.id && Array.isArray(session.messages)).slice(0, librarianHistoryLimit)
    : [];
}

function readLocalLibrarianHistory() {
  try {
    return validLibrarianSessions(JSON.parse(localStorage.getItem(librarianHistoryStorageKey) || '[]'));
  } catch (_) {
    return [];
  }
}

async function readDesktopLibrarianHistory() {
  const response = await fetch(librarianDesktopHistoryEndpoint, { cache: 'no-store' });
  if (response.status === 404) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `本机加密历史读取失败 ${response.status}`);
  if (!librarianDesktopStorageLabels.has(body.storage)) throw new Error('桌面历史没有使用预期的加密存储');
  return validLibrarianSessions(body.sessions);
}

async function persistLibrarianHistory(sessions) {
  if (librarianHistoryBackend === 'desktop-secure') {
    const response = await fetch(librarianDesktopHistoryEndpoint, desktopRequestOptions({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessions }),
    }));
    captureDesktopCsrf(response);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `本机加密历史保存失败 ${response.status}`);
    localStorage.removeItem(librarianHistoryStorageKey);
    state.runtimeWarnings.delete('本机加密对话');
    renderRuntimeWarnings();
    return;
  }
  if (librarianHistoryBackend === 'browser-local') {
    localStorage.setItem(librarianHistoryStorageKey, JSON.stringify(sessions));
  }
}

function queueLibrarianHistorySave(sessions) {
  const snapshot = JSON.parse(JSON.stringify(sessions));
  librarianHistorySaveChain = librarianHistorySaveChain
    .then(() => persistLibrarianHistory(snapshot))
    .catch(error => {
      state.runtimeWarnings.set('本机加密对话', `本机加密对话：${error.message}`);
      renderRuntimeWarnings();
    });
}

function compactLibrarianHistory() {
  let sessions = state.librarianSessions.slice(0, librarianHistoryLimit);
  while (sessions.length > 1 && JSON.stringify(sessions).length > librarianHistoryByteLimit) sessions.pop();
  state.librarianSessions = sessions;
  try {
    queueLibrarianHistorySave(sessions);
  } catch (_) {
    const lightweight = sessions.map((session, index) => index < 4 ? session : { ...session, results: [] });
    try { queueLibrarianHistorySave(lightweight); } catch (_) { /* history is optional */ }
    state.librarianSessions = lightweight;
  }
}

function saveLibrarianSession() {
  if (!state.librarianSessionId || !state.librarianMessages.length) return;
  const previous = state.librarianSessions.find(session => session.id === state.librarianSessionId);
  const session = {
    id: state.librarianSessionId,
    title: librarianSessionTitle(state.librarianMessages),
    created_at: previous?.created_at || new Date().toISOString(),
    updated_at: new Date().toISOString(),
    messages: state.librarianMessages.slice(-16),
    results: state.librarianResults,
    result_type: state.librarianResultType,
    meta: state.librarianMeta,
  };
  state.librarianSessions = [session, ...state.librarianSessions.filter(value => value.id !== session.id)];
  compactLibrarianHistory();
  renderLibrarianHistory();
}

async function loadLibrarianHistory() {
  if (isReadOnly()) {
    librarianHistoryBackend = 'readonly-none';
    state.librarianSessions = [];
    state.runtimeWarnings.delete('本机加密对话');
    renderRuntimeWarnings();
    setText('librarian-history-privacy', '只读模式不保存对话；不写科学数据库。');
    resetLibrarian({ focus: false });
    return;
  }
  try {
    const desktopSessions = await readDesktopLibrarianHistory();
    if (desktopSessions === null) {
      librarianHistoryBackend = 'browser-local';
      state.librarianSessions = readLocalLibrarianHistory();
      setText('librarian-history-privacy', '记录仅保存在当前浏览器；打开旧对话不会再次调用模型。');
    } else {
      librarianHistoryBackend = 'desktop-secure';
      const legacySessions = readLocalLibrarianHistory();
      state.librarianSessions = desktopSessions.length ? desktopSessions : legacySessions;
      if (!desktopSessions.length && legacySessions.length) await persistLibrarianHistory(legacySessions);
      localStorage.removeItem(librarianHistoryStorageKey);
      setText('librarian-history-privacy', '桌面版已在本机加密保存；不写入科学数据库。');
    }
  } catch (error) {
    librarianHistoryBackend = 'desktop-secure-error';
    state.librarianSessions = [];
    localStorage.removeItem(librarianHistoryStorageKey);
    state.runtimeWarnings.set('本机加密对话', `本机加密对话：${error.message}；为避免明文降级，本次不会保存历史。`);
    renderRuntimeWarnings();
    setText('librarian-history-privacy', '本机加密存储不可用；为避免明文降级，本次历史保存已暂停。');
  }
  if (state.librarianSessions.length) restoreLibrarianSession(state.librarianSessions[0].id, { focus: false });
  else resetLibrarian({ focus: false });
}

function resetLibrarian(options = {}) {
  if (state.librarianBusy && options.force !== true) return toast('图书管理员仍在检索，请等待本次任务完成。', true);
  clearLibrarianBriefAuthorization();
  clearLibrarianPendingSynthesis();
  clearLibrarianResearchContext();
  state.librarianSessionId = librarianSessionId();
  state.librarianMessages = [];
  state.librarianResults = [];
  state.librarianMeta = {};
  state.librarianResultType = 'item';
  document.querySelector('#librarian-input').value = '';
  renderLibrarianConversation();
  renderLibrarianResults([]);
  renderLibrarianHistory();
  setText('librarian-status', '检索范围：官方文献全库（不含我的实验）');
  if (options.focus !== false) window.requestAnimationFrame(() => document.querySelector('#librarian-input')?.focus());
}

function bindLibrarianSuggestions() {
  document.querySelectorAll('[data-librarian-suggestion]').forEach(button => button.addEventListener('click', () => {
    document.querySelector('#librarian-input').value = button.dataset.librarianSuggestion;
    document.querySelector('#librarian-form').requestSubmit();
  }));
}

function librarianReferenceHtml(ref, interactiveRefs = new Set()) {
  const normalized = String(ref || '').replace(/^\[/, '').replace(/\]$/, '');
  if (!/^R\d+$/.test(normalized)) return esc(ref);
  if (interactiveRefs.has(normalized)) {
    return `<button type="button" class="librarian-citation is-clickable" data-librarian-reference="${esc(normalized)}" aria-label="查看 ${esc(normalized)} 对应的证据卡">[${esc(normalized)}]</button>`;
  }
  return `<span class="librarian-citation historical" title="仅最新一轮回答的引用可跳转">[${esc(normalized)}]</span>`;
}

function librarianInlineMarkdown(text, options = {}) {
  const interactiveRefs = options.interactiveRefs || new Set();
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[R(\d+)\]/g, (_, number) => librarianReferenceHtml(`R${number}`, interactiveRefs));
}

function librarianMarkdown(content, options = {}) {
  const lines = String(content || '').replace(/\r/g, '').split('\n');
  const html = [];
  let list = null;
  let table = [];
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()));
    const filtered = rows.filter(row => !row.every(cell => /^:?-{3,}:?$/.test(cell)));
    if (filtered.length) {
      html.push('<div class="librarian-answer-table"><table>');
      filtered.forEach((row, index) => html.push(`<tr>${row.map(cell => `<${index === 0 ? 'th' : 'td'}>${librarianInlineMarkdown(cell, options)}</${index === 0 ? 'th' : 'td'}>`).join('')}</tr>`));
      html.push('</table></div>');
    }
    table = [];
  };
  lines.forEach(rawLine => {
    const line = rawLine.trim();
    if (/^\|.*\|$/.test(line)) { closeList(); table.push(line); return; }
    flushTable();
    if (!line) { closeList(); return; }
    if (/^-{3,}$/.test(line)) { closeList(); html.push('<hr>'); return; }
    const heading = line.match(/^(#{2,4})\s+(.+)$/);
    if (heading) { closeList(); const level = Math.min(heading[1].length + 1, 5); html.push(`<h${level}>${librarianInlineMarkdown(heading[2], options)}</h${level}>`); return; }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) { if (list !== 'ul') { closeList(); list = 'ul'; html.push('<ul>'); } html.push(`<li>${librarianInlineMarkdown(bullet[1], options)}</li>`); return; }
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) { if (list !== 'ol') { closeList(); list = 'ol'; html.push('<ol>'); } html.push(`<li>${librarianInlineMarkdown(numbered[1], options)}</li>`); return; }
    closeList();
    html.push(`<p>${librarianInlineMarkdown(line, options)}</p>`);
  });
  flushTable();
  closeList();
  return html.join('');
}

function librarianQueryChipsHtml(queryAnalysis) {
  const constraints = queryAnalysis?.constraints;
  if (!constraints || typeof constraints !== 'object') return '';
  const chips = [];
  Object.values(constraints).forEach(group => {
    const values = Array.isArray(group?.values) ? group.values : [];
    values.forEach(value => chips.push(`<span><b>${esc(group?.label || '条件')}</b>${esc(value)}</span>`));
  });
  if (!chips.length) return '';
  return `<div class="librarian-query-chips" aria-label="本轮已识别的检索条件">${chips.join('')}</div>`;
}

function librarianReportRefsHtml(refs, interactiveRefs) {
  return (Array.isArray(refs) ? refs : [])
    .filter(ref => /^R\d+$/.test(String(ref || '')))
    .map(ref => librarianReferenceHtml(ref, interactiveRefs))
    .join(' ');
}

function librarianIntentHtml(intent) {
  if (!intent || typeof intent !== 'object') return '';
  const labels = {
    system_capability: ['系统说明', '无需检索论文'],
    conversation: ['普通对话', '无需检索论文'],
    research_lookup: ['定向研究检索', '检索现有证据'],
    research_review: ['研究主题综述', '构建证据主题图'],
    followup_ref: ['证据追问', '按 R# 继续'],
    followup_bundle: ['证据组追问', '按 B# 继续'],
    clarification: ['条件澄清', '暂不执行检索'],
  };
  const [label, detail] = labels[intent.kind] || ['研究问答', '有界证据流程'];
  return `<div class="librarian-intent" data-librarian-intent="${esc(intent.kind || 'unknown')}"><span>${esc(label)}</span><b>${esc(detail)}</b></div>`;
}

function librarianReviewMapHtml(reviewMap, interactiveRefs) {
  const themes = (Array.isArray(reviewMap) ? reviewMap : []).slice(0, 6);
  if (!themes.length) return '';
  return `<aside class="librarian-review-map" aria-label="研究主题图"><header><div><span>RESEARCH MAP</span><h3>研究主题图</h3></div><b>${themes.length} 个主题</b></header><div>${themes.map(theme => `<article><span>${esc(theme?.theme_id || '')}</span><div><strong>${esc(theme?.label || '未命名主题')}</strong><small>${Number(theme?.paper_count) || 0} 篇论文 · ${Number(theme?.evidence_count) || 0} 项证据</small></div><p>${librarianReportRefsHtml(theme?.representative_refs, interactiveRefs)}</p></article>`).join('')}</div></aside>`;
}

function librarianReportHtml(report, queryAnalysis, interactiveRefs, recommendedArticles = [], intent = null, reviewMap = [], suggestedActions = []) {
  if (!report || typeof report !== 'object' || !report.direct_conclusion) return '';
  const conclusion = report.direct_conclusion || {};
  const status = ['found', 'not_found', 'clarification', 'informational'].includes(conclusion.status) ? conclusion.status : 'unknown';
  const statusLabel = { found: '找到直接证据', not_found: '未找到直接证据', clarification: '需要补充条件', informational: '本轮无需检索', unknown: '结论待核对' }[status];
  const matrix = Array.isArray(report.evidence_matrix) ? report.evidence_matrix : [];
  const related = Array.isArray(report.related_evidence) ? report.related_evidence : [];
  const gaps = Array.isArray(report.database_gaps) ? report.database_gaps : [];
  const validatedActions = (Array.isArray(suggestedActions) ? suggestedActions : [])
    .filter(action => action?.answerable && String(action?.text || '').trim())
    .slice(0, 3);
  const followups = validatedActions.length
    ? validatedActions.map(action => String(action.text))
    : (Array.isArray(report.suggested_followups) ? report.suggested_followups : []).slice(0, 3);
  const articles = (Array.isArray(recommendedArticles) ? recommendedArticles : []).slice(0, 6);
  const focus = String(queryAnalysis?.focus || '').trim();
  const conclusionTextRefs = new Set([...String(conclusion.text || '').matchAll(/\[R(\d+)\]/g)].map(match => `R${match[1]}`));
  const conclusionExtraRefs = (Array.isArray(conclusion.refs) ? conclusion.refs : []).filter(ref => !conclusionTextRefs.has(String(ref)));
  const focusHtml = focus || librarianQueryChipsHtml(queryAnalysis)
    ? `<header class="librarian-report-focus"><span>本轮问题理解</span>${focus ? `<strong>${esc(focus)}</strong>` : ''}${librarianQueryChipsHtml(queryAnalysis)}</header>`
    : '';
  const matrixRows = matrix.map(row => {
    const page = row?.source_page ? `PDF 第 ${esc(row.source_page)} 页` : '页码见证据卡';
    const paper = [row?.article_title || '未命名文章', row?.doi, page].filter(Boolean).map(esc).join(' · ');
    return `<tr><td>${esc(row?.material || '—')}</td><td>${esc(row?.conditions || '—')}</td><td>${esc(row?.property || '—')}</td><td>${esc(row?.result || '见原文证据')}</td><td><span class="librarian-matrix-paper">${paper}</span>${librarianReportRefsHtml(row?.refs, interactiveRefs)}</td></tr>`;
  }).join('');
  const relatedHtml = related.length
    ? related.map(row => {
        const relaxed = Array.isArray(row?.relaxed_constraints) ? row.relaxed_constraints.filter(Boolean) : [];
        return `<article class="librarian-related-row"><div><strong>${esc(row?.summary || '相关证据')}</strong><span>${esc(row?.article_title || '未命名文章')}${row?.bundle_id ? ` · 证据组 ${esc(row.bundle_id)}` : ''}</span></div><div class="librarian-relaxed"><b>放宽条件</b>${esc(relaxed.join('、') || '一个硬条件')}</div><div class="librarian-related-refs">${librarianReportRefsHtml(row?.refs, interactiveRefs)}</div></article>`;
      }).join('')
    : '<p class="librarian-report-empty">暂无只放宽一个硬条件即可成立的相关证据。</p>';
  const gapHtml = gaps.length
    ? `<ul>${gaps.map(value => `<li>${esc(value)}</li>`).join('')}</ul>`
    : '<p class="librarian-report-empty">本轮没有额外数据库空白说明。</p>';
  const followupHtml = followups.length
    ? `<div class="librarian-followups">${followups.map(value => `<button type="button" data-librarian-followup="${esc(value)}">${esc(value)}</button>`).join('')}</div>`
    : '<p class="librarian-report-empty">可继续补充材料、实验条件或目标物理量后追问。</p>';
  const articleLevelLabels = { direct: '直接相关', related: '相邻相关', expansion: '拓展阅读' };
  const articleTypeLabels = { item: '数据', finding: '结论', table: '表格', figure: '图片' };
  const articleHtml = articles.length
    ? `<aside class="librarian-article-recommendations" aria-label="相关文章推荐"><header><div><span>RELATED PAPERS</span><h3>数据库内相关文章</h3></div><b>${articles.length} 篇</b></header><div class="librarian-article-list">${articles.map(article => {
        const level = ['direct', 'related', 'expansion'].includes(article?.recommendation_level) ? article.recommendation_level : 'related';
        const details = [article?.first_author, article?.year, article?.doi].filter(Boolean).map(esc).join(' · ');
        const properties = (Array.isArray(article?.properties) ? article.properties : []).filter(Boolean).slice(0, 4);
        const types = (Array.isArray(article?.entity_types) ? article.entity_types : []).filter(Boolean).slice(0, 4);
        const tags = [...properties, ...types.map(value => articleTypeLabels[value] || value)];
        const coverage = article?.database_evidence_counts || {};
        const coverageSummary = `数值 ${Number(coverage.item) || 0} · 结论 ${Number(coverage.finding) || 0} · 表格 ${Number(coverage.table) || 0} · 图片 ${Number(coverage.figure) || 0}`;
        const warning = String(article?.coverage_warning || '').trim();
        return `<article class="librarian-article-card level-${esc(level)}${warning ? ' coverage-warning' : ''}"><div class="librarian-article-copy"><div class="librarian-article-kicker"><b>${esc(articleLevelLabels[level])}</b><span>${esc(Math.round(Number(article?.constraint_coverage || 0) * 100))}% 条件覆盖</span></div><h4>${esc(article?.article_title || '未命名文章')}</h4>${details ? `<small>${details}</small>` : ''}<p>${esc(article?.why_recommended || '该论文包含与当前问题相关的数据库证据。')}</p><small class="librarian-article-coverage">${esc(coverageSummary)}</small>${warning ? `<div class="librarian-article-warning"><b>覆盖预警</b>${esc(warning)}</div>` : ''}${tags.length ? `<div class="librarian-article-tags">${tags.map(value => `<span>${esc(value)}</span>`).join('')}</div>` : ''}</div><div class="librarian-article-refs">${librarianReportRefsHtml(article?.supporting_refs, interactiveRefs)}</div></article>`;
      }).join('')}</div></aside>`
    : '';
  const effectiveReviewMap = Array.isArray(reviewMap) && reviewMap.length ? reviewMap : report.review_map;
  return `<div class="librarian-answer librarian-research-report">
    ${librarianIntentHtml(intent)}
    ${focusHtml}
    <section class="librarian-report-section report-direct status-${esc(status)}"><header><i>01</i><div><span>DIRECT CONCLUSION</span><h3>直接结论</h3></div><b>${esc(statusLabel)}</b></header><div class="librarian-report-body">${librarianMarkdown(conclusion.text || '未形成直接结论。', { interactiveRefs })}${librarianReportRefsHtml(conclusionExtraRefs, interactiveRefs)}</div></section>
    <section class="librarian-report-section report-matrix"><header><i>02</i><div><span>EVIDENCE MATRIX</span><h3>证据矩阵</h3></div><b>${matrix.length} 组</b></header>${matrix.length ? `<div class="librarian-report-table"><table><thead><tr><th>材料</th><th>辐照/实验条件</th><th>性质</th><th>结果</th><th>论文证据</th></tr></thead><tbody>${matrixRows}</tbody></table></div>` : '<p class="librarian-report-empty">当前没有满足全部硬条件、可进入证据矩阵的记录。</p>'}</section>
    <section class="librarian-report-section report-related"><header><i>03</i><div><span>RELATED EVIDENCE</span><h3>相关证据</h3></div><b>${related.length} 项</b></header><div class="librarian-related-list">${relatedHtml}</div></section>
    <section class="librarian-report-section report-gaps"><header><i>04</i><div><span>DATABASE GAPS</span><h3>数据库空白</h3></div><b>${gaps.length} 项</b></header><div class="librarian-report-body">${gapHtml}</div></section>
    <section class="librarian-report-section report-followups"><header><i>05</i><div><span>NEXT QUESTIONS</span><h3>建议追问</h3></div><b>${followups.length} 项</b></header>${followupHtml}</section>
    ${librarianReviewMapHtml(effectiveReviewMap, interactiveRefs)}
    ${articleHtml}
  </div>`;
}

function librarianMessageHtml(message, index, activeAssistantIndex) {
  const role = message?.role || 'assistant';
  const content = message?.content || '';
  const active = role === 'assistant' && index === activeAssistantIndex;
  const report = message?.report || (active ? state.librarianMeta?.report : null);
  const queryAnalysis = message?.query_analysis || (active ? state.librarianMeta?.query_analysis : null);
  const recommendedArticles = message?.recommended_articles || (active ? state.librarianMeta?.recommended_articles : []);
  const intent = message?.intent || (active ? state.librarianMeta?.intent : null);
  const reviewMap = message?.review_map || (active ? state.librarianMeta?.review_map : []);
  const suggestedActions = message?.suggested_actions || (active ? state.librarianMeta?.suggested_actions : []);
  const currentRefs = new Set((state.librarianResults || []).map(row => String(row.agent_ref || '')).filter(Boolean));
  const answerRefs = new Set([
    ...[...String(content || '').matchAll(/\[R(\d+)\]/g)].map(match => `R${match[1]}`),
    ...[...JSON.stringify(report || {}).matchAll(/"R(\d+)"/g)].map(match => `R${match[1]}`),
  ]);
  const protocolLeak = role === 'assistant' && /DSML|tool_calls|<\|\|.*invoke/i.test(String(content || ''));
  const orphanReferences = active && answerRefs.size > 0 && [...answerRefs].some(ref => !currentRefs.has(ref));
  const guarded = protocolLeak || orphanReferences;
  const visibleContent = protocolLeak
    ? '这次回答因模型工具协议异常而未完成，内部检索指令已隐藏。请重新发送问题以生成中文总结。'
    : orphanReferences
      ? '这条历史回答没有绑定可核对的数据库证据，已停止展示。请重新发送问题；新回答将强制执行本轮检索。'
      : content;
  const interactiveRefs = active && !guarded ? currentRefs : new Set();
  const structured = role === 'assistant' && !guarded
    ? librarianReportHtml(report, queryAnalysis, interactiveRefs, recommendedArticles, intent, reviewMap, suggestedActions)
    : '';
  const answerBody = structured || (role === 'assistant' && !guarded
    ? `<div class="librarian-answer">${librarianMarkdown(visibleContent, { interactiveRefs })}</div>`
    : `<p>${esc(visibleContent).replace(/\n/g, '<br>')}</p>`);
  const historyNote = role === 'assistant' && !active && !guarded && answerRefs.size
    ? '<div class="librarian-history-answer-note">历史回答 · 引用仅作记录，不与当前一轮证据卡联动</div>'
    : '';
  const recovery = role === 'assistant' && message?.recovery_question
    ? `<div class="librarian-state-recovery"><strong>可以安全恢复</strong><span>旧研究状态已停止使用。重新检索上一研究问题后，会生成新的 R# 与 B#。</span><button type="button" data-librarian-followup="${esc(message.recovery_question)}">重新建立研究状态</button></div>`
    : '';
  const body = `${historyNote}${answerBody}${recovery}`;
  return `<article class="librarian-message ${esc(role)}${guarded ? ' protocol-guard' : ''}${active ? ' active-answer' : ''}"><span>${role === 'user' ? '你' : '馆'}</span><div class="librarian-message-body">${body}</div></article>`;
}

function focusLibrarianReference(ref) {
  const row = state.librarianResults.find(value => String(value.agent_ref) === String(ref));
  if (!row) return toast('这条引用不属于当前一轮结果，请重新发送该问题。', true);
  setLibrarianResultType(row.agent_entity_type);
  window.requestAnimationFrame(() => {
    const card = document.querySelector(`[data-agent-result-ref="${String(ref).replace(/[^R0-9]/g, '')}"]`);
    if (!card) return;
    card.classList.add('citation-target');
    card.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
    setTimeout(() => card.classList.remove('citation-target'), 1800);
  });
}

function bindLibrarianConversationActions() {
  document.querySelectorAll('[data-librarian-reference]').forEach(button => button.addEventListener('click', () => focusLibrarianReference(button.dataset.librarianReference)));
  document.querySelectorAll('[data-librarian-followup]').forEach(button => button.addEventListener('click', () => {
    const input = document.querySelector('#librarian-input');
    input.value = button.dataset.librarianFollowup || '';
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    document.querySelector('#librarian-form').requestSubmit();
  }));
}

function renderLibrarianConversation() {
  const el = document.querySelector('#librarian-messages');
  if (!state.librarianMessages.length) {
    el.innerHTML = `<div class="librarian-welcome"><strong>可以这样问：</strong><button type="button" data-librarian-suggestion="高熵合金在中子辐照后，硬度和缺陷结构有哪些变化？">高熵合金中子辐照后的硬度与缺陷</button><button type="button" data-librarian-suggestion="钨及钨合金在高温离子辐照下报告了哪些空洞或气泡结果？">钨合金高温辐照下的空洞与气泡</button></div>`;
    bindLibrarianSuggestions();
    return;
  }
  const lastIndex = state.librarianMessages.length - 1;
  const activeAssistantIndex = !state.librarianBusy && state.librarianMessages[lastIndex]?.role === 'assistant' ? lastIndex : -1;
  el.innerHTML = state.librarianMessages.map((message, index) => librarianMessageHtml(message, index, activeAssistantIndex)).join('');
  bindLibrarianConversationActions();
  el.scrollTop = el.scrollHeight;
}

function renderLibrarianHistory() {
  const el = document.querySelector('#librarian-history-list');
  if (!el) return;
  const query = state.librarianHistoryQuery.trim().toLocaleLowerCase('zh-CN');
  const sessions = state.librarianSessions.filter(session => !query || `${session.title} ${session.messages.map(message => message.content).join(' ')}`.toLocaleLowerCase('zh-CN').includes(query));
  if (!sessions.length) {
    el.innerHTML = `<div class="librarian-history-empty">${query ? '没有匹配的历史对话' : '完成第一次检索后会保存在这里'}</div>`;
    return;
  }
  el.innerHTML = sessions.map(session => {
    const active = session.id === state.librarianSessionId;
    const stamp = new Date(session.updated_at || session.created_at || Date.now()).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const resultCount = Array.isArray(session.results) ? session.results.length : 0;
    return `<article class="librarian-history-item${active ? ' active' : ''}"><button type="button" data-librarian-session="${esc(session.id)}"><strong>${esc(session.title)}</strong><span>${esc(stamp)} · ${resultCount} 项证据</span></button><button type="button" class="librarian-history-delete" data-librarian-session-delete="${esc(session.id)}" aria-label="删除对话">×</button></article>`;
  }).join('');
  el.querySelectorAll('[data-librarian-session]').forEach(button => button.addEventListener('click', () => restoreLibrarianSession(button.dataset.librarianSession)));
  el.querySelectorAll('[data-librarian-session-delete]').forEach(button => button.addEventListener('click', () => deleteLibrarianSession(button.dataset.librarianSessionDelete)));
}

function withLibrarianCitationFlags(results, messages) {
  const latestAnswer = [...(messages || [])].reverse().find(message => message.role === 'assistant') || {};
  const cited = new Set([
    ...[...String(latestAnswer.content || '').matchAll(/\[R(\d+)/g)].map(match => `R${match[1]}`),
    ...[...JSON.stringify(latestAnswer.report || {}).matchAll(/"R(\d+)"/g)].map(match => `R${match[1]}`),
  ]);
  return (results || []).map(row => row.agent_cited == null ? { ...row, agent_cited: cited.has(row.agent_ref) } : row);
}

function restoreLibrarianSession(sessionId, options = {}) {
  if (state.librarianBusy) return toast('图书管理员仍在检索，请等待本次任务完成。', true);
  const session = state.librarianSessions.find(value => value.id === sessionId);
  if (!session) return;
  clearLibrarianBriefAuthorization();
  clearLibrarianPendingSynthesis();
  state.librarianSessionId = session.id;
  state.librarianMessages = session.messages || [];
  state.librarianResults = withLibrarianCitationFlags(session.results, state.librarianMessages);
  state.librarianMeta = session.meta?.pending
    ? { error: '上一次请求在完成前中断，未保留旧证据卡。', scope: 'all' }
    : (session.meta || {});
  state.librarianResultType = session.result_type || 'item';
  document.querySelector('#librarian-input').value = '';
  renderLibrarianConversation();
  renderLibrarianResults(state.librarianResults);
  renderLibrarianHistory();
  const meta = state.librarianMeta;
  const counts = meta.match_counts || {};
  const evidenceSummary = Number.isFinite(Number(counts.direct))
    ? `直接 ${Number(counts.direct) || 0} · 相关 ${Number(counts.adjacent) || 0} · 扩展 ${Number(counts.expansion) || 0}`
    : `${meta.candidate_count ?? state.librarianResults.length} 项候选 · 回答引用 ${meta.cited_count || 0} 项`;
  const intentKind = String(meta.intent?.kind || '');
  setText('librarian-status', ['system_capability', 'conversation'].includes(intentKind)
    ? '已恢复本地对话回答；本轮没有检索论文证据'
    : meta.safe_failure_code
      ? '已恢复状态失效提示；重新检索后才能继续使用 R# 或 B#'
      : meta.model
        ? `${meta.model} · ${evidenceSummary} · 已恢复历史结果，未重新调用模型`
        : '已恢复历史对话；未重新调用模型');
  if (options.focus !== false) window.requestAnimationFrame(() => document.querySelector('#librarian-input')?.focus());
}

function deleteLibrarianSession(sessionId) {
  if (state.librarianBusy) return toast('检索进行中，暂时不能删除当前对话。', true);
  clearLibrarianResearchContext(sessionId);
  state.librarianSessions = state.librarianSessions.filter(session => session.id !== sessionId);
  compactLibrarianHistory();
  if (state.librarianSessionId === sessionId) {
    if (state.librarianSessions.length) restoreLibrarianSession(state.librarianSessions[0].id, { focus: false });
    else resetLibrarian({ focus: false });
  } else renderLibrarianHistory();
}

function librarianResultGroups(rows) {
  const groups = { item: [], table: [], figure: [], finding: [] };
  rows.forEach(row => (groups[row.agent_entity_type] || groups.item).push(row));
  Object.values(groups).forEach(values => values.sort((left, right) => {
    const leftClass = librarianMatchOrder[left.agent_match_class] ?? 9;
    const rightClass = librarianMatchOrder[right.agent_match_class] ?? 9;
    return leftClass - rightClass || Number(Boolean(right.agent_cited)) - Number(Boolean(left.agent_cited));
  }));
  return groups;
}

function preferredLibrarianResultType(groups) {
  const ranked = librarianResultTypes.map((type, priority) => ({
    type,
    priority,
    directCited: groups[type].filter(row => row.agent_match_class === 'direct' && row.agent_cited).length,
    direct: groups[type].filter(row => row.agent_match_class === 'direct').length,
    adjacentCited: groups[type].filter(row => row.agent_match_class === 'adjacent' && row.agent_cited).length,
    adjacent: groups[type].filter(row => row.agent_match_class === 'adjacent').length,
    cited: groups[type].filter(row => row.agent_cited).length,
    total: groups[type].length,
  })).filter(value => value.total);
  ranked.sort((left, right) => right.directCited - left.directCited || right.direct - left.direct || right.adjacentCited - left.adjacentCited || right.cited - left.cited || right.adjacent - left.adjacent || left.priority - right.priority);
  return ranked[0]?.type || 'item';
}

function librarianResultBadge(row) {
  const cited = Boolean(row.agent_cited);
  const matchClass = row.agent_match_class;
  const bundle = String(row.agent_bundle_id || '').trim();
  const relaxed = String(row.agent_relaxed_condition || '').trim();
  let label = cited ? '回答引用' : '扩展候选';
  if (matchClass === 'direct') label = cited ? '直接证据 · 报告引用' : '直接候选';
  else if (matchClass === 'adjacent') label = `相关证据${relaxed ? ` · 放宽${relaxed}` : ''}`;
  else if (matchClass === 'expansion') label = '扩展候选';
  if (bundle) label += ` · 证据组 ${bundle}`;
  const roleClass = ['direct', 'adjacent', 'expansion'].includes(matchClass) ? matchClass : (cited ? 'legacy-cited' : 'expansion');
  return {
    roleClass,
    html: `<b class="agent-result-ref ${esc(roleClass)}${cited ? ' cited' : ' candidate'}">[${esc(row.agent_ref)}] · ${esc(label)}</b>`,
  };
}

function renderLibrarianResults(rows) {
  const el = document.querySelector('#search-results');
  if (state.searchExperience !== 'agent') return;
  const tabs = document.querySelector('#librarian-result-tabs');
  if (!rows.length) {
    tabs.hidden = true;
    const meta = state.librarianMeta || {};
    const intentKind = String(meta.intent?.kind || '');
    const empty = meta.pending
      ? ['⌛', '正在准备本轮证据', '旧结果已清空；新回答完成前不会把上一轮卡片挂到当前问题下。']
      : ['system_capability', 'conversation'].includes(intentKind)
        ? ['✓', intentKind === 'system_capability' ? '系统说明已完成' : '对话回答已完成', '本轮不需要检索论文证据，因此没有证据卡片；这不是检索失败。']
      : meta.safe_failure_code
        ? ['↻', '需要重新建立研究状态', '旧 R# 或 B# 已停止使用；请使用上方恢复按钮重新检索原研究问题。']
      : meta.clarification_required
        ? ['?', '需要先补充问题条件', '图书管理员尚未执行证据检索；请选择建议追问或明确材料、实验条件与物理量。']
        : meta.error
          ? ['!', '本次检索未完成', '没有沿用上一轮证据。你可以修改问题重试，或切换到精确检索。']
          : meta.report || meta.summary_mode === 'no_results'
            ? ['∅', '本轮没有可核对的候选证据', '回答中的数据库空白说明保留在上方；系统不会生成不存在的论文或数据。']
            : ['⌕', '等待你的研究问题', '图书管理员会返回现有四类证据，不会生成数据库中不存在的论文或数据。'];
    el.innerHTML = `<div class="blank search-empty librarian-empty"><span>${empty[0]}</span><h3>${empty[1]}</h3><p>${empty[2]}</p></div>`;
    return;
  }
  const groups = librarianResultGroups(rows);
  const labels = { item: '数据条目', table: '原始表格', figure: '论文图片', finding: '实验结论' };
  const available = librarianResultTypes.filter(type => groups[type].length);
  if (!available.includes(state.librarianResultType)) state.librarianResultType = available[0] || 'item';
  tabs.hidden = false;
  const citedTotal = rows.filter(row => row.agent_cited).length;
  const hasReasoningRoles = rows.some(row => ['direct', 'adjacent', 'expansion'].includes(row.agent_match_class));
  const counts = hasReasoningRoles ? {
    direct: rows.filter(row => row.agent_match_class === 'direct').length,
    adjacent: rows.filter(row => row.agent_match_class === 'adjacent').length,
    expansion: rows.filter(row => row.agent_match_class === 'expansion').length,
  } : null;
  setText('librarian-result-overview', counts
    ? `共 ${rows.length} 项 · 直接 ${counts.direct} · 相关 ${counts.adjacent} · 扩展 ${counts.expansion} · 报告引用 ${citedTotal}`
    : `共召回 ${rows.length} 项 · 回答引用 ${citedTotal} 项`);
  document.querySelectorAll('[data-librarian-result-type]').forEach(button => {
    const type = button.dataset.librarianResultType;
    const active = type === state.librarianResultType;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
    button.disabled = groups[type].length === 0;
    const directCount = groups[type].filter(row => row.agent_match_class === 'direct').length;
    const relatedCount = groups[type].filter(row => row.agent_match_class === 'adjacent').length;
    button.setAttribute('aria-label', `${labels[type]} ${groups[type].length} 项${hasReasoningRoles ? `，直接 ${directCount}，相关 ${relatedCount}` : ''}`);
    setText(`librarian-count-${type}`, groups[type].length);
  });
  const type = state.librarianResultType;
  const values = groups[type];
  const cards = values.map(row => {
      const badge = librarianResultBadge(row);
      const cardAttributes = `data-agent-result-ref="${esc(row.agent_ref)}"`;
      if (type === 'item') return itemResultHtml(row).replace('<article class="result"', `<article class="result agent-result match-${esc(badge.roleClass)}" ${cardAttributes}`).replace('<div class="result-value-block">', `${badge.html}<div class="result-value-block">`);
      if (type === 'finding') return `<article class="result qualitative-result agent-result match-${esc(badge.roleClass)}" ${cardAttributes} data-item-result="${esc(row.item_id)}">${badge.html}<div class="finding-mark"><small>QUALITATIVE</small><span>实验结论</span></div><div class="result-content"><div class="result-heading"><div><small>具体意义</small><h3>${esc(row.meaning || '实验结论')}</h3></div></div><div class="finding-text"><p>${esc(row.finding_text || row.value_text)}</p></div><div class="result-context"><small>实验条件与文章语境</small><p>${esc(row.context_explanation || '')}</p></div><footer><div class="result-paper"><strong>${esc(row.article_title)}</strong><span>${esc(row.doi || '')} · PDF 第 ${esc(row.source_page || '?')} 页</span></div><div class="result-actions"><button class="row-link detail-link" data-agent-finding="${esc(row.item_id)}">打开条目</button><button class="row-link source-link" data-source-finding="${esc(row.item_id)}">原文证据</button></div></footer></div></article>`;
      return `<article class="visual-result agent-result match-${esc(badge.roleClass)}" ${cardAttributes}>${badge.html}<button class="visual-thumb visual-thumb-${esc(type)}" type="button" data-visual-open="${esc(row.id)}"><img src="${esc(row.image_url)}" alt="${esc(row.display_name || row.label)}原文截图" loading="lazy"><span>${type === 'table' ? '完整表格' : '完整图片'}</span></button><div class="visual-result-copy"><div class="visual-result-title"><span>${esc(row.label)} · PDF第 ${esc(row.page_start)} 页</span><h3>${esc(row.display_name || row.label)}</h3></div><p>${esc(row.context_explanation || row.caption || '')}</p><small>${esc(row.article_title)} · ${esc(row.doi || '无 DOI')}</small><div class="visual-result-actions"><button class="open-visual detail-link" type="button" data-visual-open="${esc(row.id)}">打开详情与 AI 解读</button></div></div></article>`;
    }).join('');
  el.innerHTML = `<section class="agent-result-group" aria-label="${esc(labels[type])}">${cards}</section>`;
  el.querySelectorAll('[data-visual-open]').forEach(button => button.addEventListener('click', () => openVisualAsset(Number(button.dataset.visualOpen))));
  el.querySelectorAll('[data-source-search]').forEach(button => button.addEventListener('click', () => {
    const itemId = Number(button.dataset.sourceSearch);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
  el.querySelectorAll('[data-item-detail]').forEach(button => button.addEventListener('click', () => {
    const row = rows.find(value => Number(value.item_id) === Number(button.dataset.itemDetail));
    openItemDetail(row);
  }));
  el.querySelectorAll('[data-agent-finding]').forEach(button => button.addEventListener('click', () => {
    const row = rows.find(value => Number(value.item_id) === Number(button.dataset.agentFinding));
    openItemDetail(row, { qualitative: true });
  }));
  el.querySelectorAll('[data-source-finding]').forEach(button => button.addEventListener('click', () => {
    const itemId = Number(button.dataset.sourceFinding);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
}

function setLibrarianResultType(type) {
  if (!librarianResultTypes.includes(type)) return;
  state.librarianResultType = type;
  renderLibrarianResults(state.librarianResults);
  saveLibrarianSession();
}

function updateLibrarianProgress() {
  const elapsed = Math.max(0, (Date.now() - state.librarianProgressStarted) / 1000);
  const stages = [
    [0, '预计：正在理解研究问题'],
    [3, '预计：正在整理材料、条件与物理量'],
    [8, '预计：正在穿梭全库书架检索四类证据'],
    [16, '预计：正在核对候选记录与原文位置'],
    [28, '预计：正在组织回答与证据引用'],
  ];
  const stage = [...stages].reverse().find(([second]) => elapsed >= second)?.[1] || stages[0][1];
  const percent = Math.min(94, Math.round(8 + 88 * (1 - Math.exp(-elapsed / 18))));
  if (state.librarianProgressStage !== stage) {
    state.librarianProgressStage = stage;
    setText('librarian-progress-stage', stage);
  }
  const wholeSeconds = Math.floor(elapsed);
  if (state.librarianProgressSecond !== wholeSeconds) {
    state.librarianProgressSecond = wholeSeconds;
    setText('librarian-progress-time', `${wholeSeconds} 秒`);
  }
  document.querySelector('#librarian-progress-bar').style.width = `${percent}%`;
}

function startLibrarianProgress() {
  const el = document.querySelector('#librarian-progress');
  clearInterval(state.librarianProgressTimer);
  state.librarianProgressStarted = Date.now();
  state.librarianProgressStage = '';
  state.librarianProgressSecond = -1;
  el.hidden = false;
  el.classList.remove('done', 'failed');
  document.querySelector('#librarian-progress-bar').style.width = '8%';
  updateLibrarianProgress();
  state.librarianProgressTimer = setInterval(updateLibrarianProgress, 1000);
}

function finishLibrarianProgress(success, label = '') {
  clearInterval(state.librarianProgressTimer);
  state.librarianProgressTimer = null;
  const started = state.librarianProgressStarted;
  const el = document.querySelector('#librarian-progress');
  el.classList.add(success ? 'done' : 'failed');
  setText('librarian-progress-stage', label || (success ? '回答与证据已就绪' : '本次检索未完成'));
  document.querySelector('#librarian-progress-bar').style.width = '100%';
  setTimeout(() => {
    if (state.librarianProgressStarted === started && !state.librarianBusy) el.hidden = true;
  }, 700);
}

function applyLibrarianFinalResult(result, context) {
    if (!result || result.librarian_core_version !== 'librarian-v3' || typeof result.answer !== 'string') {
      throw new Error('图书管理员最终结果无效。');
    }
    const { question, recoveryQuestion } = context;
    clearLibrarianPendingSynthesis();
    const stateFailureCode = librarianStateFailureCode(result);
    if (stateFailureCode) clearLibrarianResearchContext();
    else rememberLibrarianResearchContext(result);
    state.librarianMessages.push({ role: 'user', content: question });
    state.librarianMessages.push({
      role: 'assistant',
      content: result.answer,
      report: result.report || null,
      query_analysis: result.query_analysis || null,
      recommended_articles: result.recommended_articles || [],
      intent: result.intent || null,
      review_map: result.review_map || result.report?.review_map || [],
      suggested_actions: result.suggested_actions || [],
      recovery_question: stateFailureCode ? recoveryQuestion : '',
    });
    state.librarianResults = result.results || [];
    state.librarianMeta = {
      model: result.model,
      tool_calls: result.tool_calls,
      recall_queries: (result.recall_queries || []).length,
      candidate_count: result.candidate_count ?? state.librarianResults.length,
      cited_count: result.cited_count ?? state.librarianResults.filter(row => row.agent_cited).length,
      summary_mode: result.summary_mode,
      cache_hit: Boolean(result.cache_hit),
      report: result.report || null,
      query_analysis: result.query_analysis || null,
      evidence_bundles: result.evidence_bundles || [],
      recommended_articles: result.recommended_articles || [],
      recommended_article_count: result.recommended_article_count || 0,
      intent: result.intent || null,
      retrieval_policy: result.retrieval_policy || '',
      review_map: result.review_map || result.report?.review_map || [],
      suggested_actions: result.suggested_actions || [],
      safe_failure_code: stateFailureCode,
      match_counts: result.match_counts || null,
      bundle_count: result.bundle_count || 0,
      clarification_required: Boolean(result.clarification_required),
      response_format: result.response_format || result.report?.schema_version || 'legacy',
      scope: 'all',
    };
    state.librarianBriefAuth = {
      session_id: state.librarianSessionId,
      assistant_index: state.librarianMessages.length - 1,
      envelope: result.research_brief || null,
      plan_mode: result.plan_mode || '',
      answered_at: result.answered_at || '',
      evidence_version: result.evidence_version || '',
    };
    const groups = librarianResultGroups(state.librarianResults);
    state.librarianResultType = preferredLibrarianResultType(groups);
    const summaryLabel = stateFailureCode
      ? '旧研究状态已停止使用，可重新建立'
      : result.clarification_required
      ? '需要补充条件，尚未执行证据检索'
      : result.cache_hit
        ? '已复用相同证据版本的稳定结果'
        : result.summary_mode === 'deterministic_fallback'
          ? '总结已降级，候选仍可核对'
          : `${aiProviderLabel()} 已完成筛选总结`;
    const matchCounts = result.match_counts || {};
    const matchSummary = Number.isFinite(Number(matchCounts.direct))
      ? `直接 ${Number(matchCounts.direct) || 0} · 相关 ${Number(matchCounts.adjacent) || 0} · 扩展 ${Number(matchCounts.expansion) || 0}`
      : `召回 ${state.librarianResults.length} 项 · 引用 ${result.cited_count || 0} 项`;
    const intentKind = String(result.intent?.kind || '');
    const localOnly = ['system_capability', 'conversation'].includes(intentKind);
    setText('librarian-status', localOnly
      ? `本地回答 · ${intentKind === 'system_capability' ? '系统能力说明' : '普通对话'} · 未检索论文证据`
      : `${result.model} · ${matchSummary} · ${summaryLabel}`);
    setText('librarian-progress-stage', '已收到结果，正在组织回答与证据卡片');
    document.querySelector('#librarian-progress-bar').style.width = '96%';
    renderLibrarianResults(state.librarianResults);
}

function failLibrarianRequest(error, recoveryQuestion = '') {
    clearLibrarianBriefAuthorization();
    clearLibrarianPendingSynthesis();
    clearLibrarianResearchContext();
    const failureCode = librarianTransportFailureCode(error);
    state.librarianMeta = { error: true, safe_failure_code: failureCode };
    state.librarianResults = [];
    setText('librarian-status', '图书管理员暂时不可用；精确检索仍可正常使用');
    renderLibrarianResults([]);
    toast('图书管理员暂时无法完成本次请求，请稍后重试。', true);
}

async function submitLibrarian(event) {
  event.preventDefault();
  if (state.librarianBusy) return;
  clearLibrarianPendingSynthesis();
  const input = document.querySelector('#librarian-input');
  const question = input.value.trim();
  if (!question) return toast('请先描述你想查找的问题。', true);
  const history = state.librarianMessages.slice(-8).map(message => ({ role: message.role, content: message.content }));
  const context = { question, history, recoveryQuestion: [...history].reverse().find(message => message.role === 'user')?.content || '' };
  const requestPayload = librarianResearchRequest(question, history);
  let authorization;
  try {
    authorization = await authorizePreparedAIAction('librarian', 'librarian', requestPayload);
  } catch (_error) {
    return toast('图书管理员暂时无法开始 AI 请求；精确检索仍可正常使用。', true);
  }
  if (!authorization) return toast(`已取消，未向 ${aiProviderLabel()} 发送任何内容。`);
  clearLibrarianBriefAuthorization();
  state.librarianBusy = true;
  document.querySelector('#librarian-send').disabled = true;
  setText('librarian-status', authorization.local_result ? '正在整理本地回答 · 不调用模型' : '阶段 1/2 · 正在规划检索（最多调用模型 1 次）');
  startLibrarianProgress();
  let success = false;
  let preserveStage = false;
  let finalApplied = false;
  try {
    const result = authorization.local_result
      || await globalThis.AutoResearchDesktopPorts.executePreparedAIAction('librarian', authorization.action_id, authorization.consent_nonce);
    if (validLibrarianSynthesisStage(result)) {
      showLibrarianSynthesisStage(result, context);
      input.value = '';
      preserveStage = true;
      success = true;
    } else {
      applyLibrarianFinalResult(result, context);
      input.value = '';
      finalApplied = true;
      success = true;
    }
  } catch (error) {
    failLibrarianRequest(error, context.recoveryQuestion);
  } finally {
    state.librarianBusy = false;
    document.querySelector('#librarian-send').disabled = false;
    renderLibrarianConversation();
    finishLibrarianProgress(success, preserveStage ? '阶段 1 已完成，等待你决定是否生成回答' : success && state.librarianMeta.clarification_required ? '需要补充问题条件' : '');
    if (finalApplied) saveLibrarianSession();
  }
}

async function continueLibrarianSynthesis() {
  const pending = librarianPendingSynthesis;
  if (!pending || state.librarianBusy || pending.sessionId !== state.librarianSessionId) return;
  const generation = pending.generation;
  let authorization;
  try {
    authorization = await authorizePreparedAIAction('librarian', 'librarian', { job_token: pending.jobToken });
  } catch (_error) {
    return toast('回答生成暂时无法开始；阶段 1 结果仍在本页，可稍后重试。', true);
  }
  if (!authorization) {
    clearLibrarianPendingSynthesis('已停止，未进行第二次收费调用。');
    return toast(`已取消，未向 ${aiProviderLabel()} 发起第二次模型调用。`);
  }
  if (generation !== librarianStageGeneration || pending !== librarianPendingSynthesis) return;
  state.librarianBusy = true;
  document.querySelector('#librarian-send').disabled = true;
  document.querySelector('#librarian-synthesis-continue').disabled = true;
  setText('librarian-status', '阶段 2/2 · 正在生成证据回答（最多调用模型 1 次）');
  startLibrarianProgress();
  let success = false;
  let finalApplied = false;
  try {
    const result = await globalThis.AutoResearchDesktopPorts.executePreparedAIAction('librarian', authorization.action_id, authorization.consent_nonce);
    if (generation !== librarianStageGeneration || pending !== librarianPendingSynthesis) return;
    applyLibrarianFinalResult(result, pending);
    finalApplied = true;
    success = true;
  } catch (error) {
    failLibrarianRequest(error, pending.recoveryQuestion);
  } finally {
    state.librarianBusy = false;
    document.querySelector('#librarian-send').disabled = false;
    const button = document.querySelector('#librarian-synthesis-continue');
    if (button) button.disabled = false;
    renderLibrarianConversation();
    finishLibrarianProgress(success);
    if (finalApplied) saveLibrarianSession();
  }
}

function getLatestLibrarianBriefSnapshot() {
  const briefAuth = state.librarianBriefAuth;
  if (
    state.librarianBusy
    || state.librarianMeta?.pending
    || state.librarianMeta?.error
    || state.librarianMeta?.clarification_required
    || briefAuth?.session_id !== state.librarianSessionId
    || briefAuth?.assistant_index !== state.librarianMessages.length - 1
    || !briefAuth?.envelope?.eligible
    || !briefAuth?.envelope?.snapshot_token
  ) return null;
  const assistantIndex = state.librarianMessages.length - 1;
  const assistant = state.librarianMessages[assistantIndex];
  if (assistant?.role !== 'assistant' || !assistant.report || typeof assistant.report !== 'object') return null;
  if (
    assistant.report?.direct_conclusion?.status === 'clarification'
    || /DSML|tool_calls|<\|\|.*invoke/i.test(
      `${String(assistant.content || '')} ${JSON.stringify(assistant.report || {})}`
    )
  ) return null;
  let question = '';
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    const message = state.librarianMessages[index];
    if (message?.role === 'user' && String(message.content || '').trim()) {
      question = String(message.content).trim();
      break;
    }
  }
  if (!question) return null;
  return JSON.parse(JSON.stringify({
    question,
    report: assistant.report,
    query_analysis: assistant.query_analysis || state.librarianMeta.query_analysis || null,
    results: state.librarianResults,
    model: state.librarianMeta.model || '',
    plan_mode: briefAuth.plan_mode || '',
    summary_mode: state.librarianMeta.summary_mode || '',
    candidate_count: state.librarianMeta.candidate_count ?? state.librarianResults.length,
    cited_count: state.librarianMeta.cited_count ?? state.librarianResults.filter(row => row.agent_cited).length,
    match_counts: state.librarianMeta.match_counts || null,
    bundle_count: state.librarianMeta.bundle_count || 0,
    clarification_required: Boolean(state.librarianMeta.clarification_required),
    response_format: state.librarianMeta.response_format || '',
    cache_hit: Boolean(state.librarianMeta.cache_hit),
    answered_at: briefAuth.answered_at || briefAuth.envelope.answered_at || '',
    evidence_version: briefAuth.evidence_version || briefAuth.envelope.evidence_fingerprint || '',
  }));
}

function getLatestLibrarianBriefPayload() {
  const snapshot = getLatestLibrarianBriefSnapshot();
  if (!snapshot) return null;
  return {
    snapshot,
    snapshot_token: state.librarianBriefAuth.envelope.snapshot_token,
  };
}

function clearLibrarianBriefAuthorization(expectedToken = '') {
  const currentToken = state.librarianBriefAuth?.envelope?.snapshot_token || '';
  if (expectedToken && currentToken !== expectedToken) return;
  state.librarianBriefAuth = null;
}

globalThis.autoResearchLibrarianBrief = Object.freeze({
  getLatestSnapshot: getLatestLibrarianBriefSnapshot,
  getLatestPayload: getLatestLibrarianBriefPayload,
  clearAuthorization: clearLibrarianBriefAuthorization,
});

function setSearchMode(mode, options = {}) {
  if (!searchModeCopy[mode]) return;
  invalidateEvidenceInspector();
  state.searchMode = mode;
  document.querySelectorAll("[data-search-mode]").forEach(button => {
    const active = button.dataset.searchMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  const input = document.querySelector("#search-query");
  input.placeholder = searchModeCopy[mode].placeholder;
  setText("search-help", searchModeCopy[mode].help);
  document.querySelector("#search-exports").hidden = !["item", "finding"].includes(mode);
  document.querySelector("#search-filter-bar").hidden = false;
  ["search-source-filter", "search-sort"].forEach(id => {
    document.querySelector(`#${id}`)?.closest("label")?.toggleAttribute("hidden", mode !== "item");
  });
  renderSearchSuggestions();
  renderActiveFilters();
  globalThis.AutoResearchDesktopProduct?.applySearchUI?.();
  if (options.run !== false) runSearch();
}

function searchReviewLabel(row) {
  if (row.origin_type === "manual") return "人工补录";
  return {
    confirmation: "历史确认",
    correction: "历史修正",
    automatic: "自动收录",
  }[row.review_action] || "自动收录";
}

function qualityBadge(row) {
  const status = row.quality_gate_status || "legacy_stable";
  const score = row.quality_score == null ? "" : ` · ${Math.round(Number(row.quality_score))}分`;
  return `<span class="quality-badge ${esc(status)}">${esc(qualityGateLabel(status))}${esc(score)}</span>`;
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

function scientificHtml(value) {
  const normalized = String(value ?? "")
    .replace(/\$/g, "")
    .replace(/\\times\b/g, "×")
    .replace(/\\cdot\b/g, "·")
    .replace(/\\pm\b/g, "±")
    .replace(/\\mu\b/g, "μ")
    .replace(/\*\s*10\s*\^/g, "× 10^");
  return esc(normalized)
    .replace(/\^\{([+\-−]?\d+)\}/g, "<sup>$1</sup>")
    .replace(/\^([+\-−]?\d+)/g, "<sup>$1</sup>");
}

function scientificQuantityHtml(value, unit) {
  const raw = [value, unit].filter(Boolean).join(" ");
  return `<span class="science-value">${scientificHtml(value)}</span>${String(unit || "").trim() ? `<span class="science-unit">${scientificHtml(unit)}</span>` : ""}<span class="visually-hidden">${esc(raw)}</span>`;
}

function visualTitleParts(asset) {
  let focus = String(asset.display_name || asset.label || "图表证据").trim();
  if (/名义.*实测.*成分/.test(focus) && !/对比/.test(focus)) focus = focus.replace(/名义\s*(?:与|和|—|-)\s*实测/, "名义—实测").replace(/成分$/, "成分对比");
  const materials = [...new Set((asset.materials || []).map(value => String(value).trim()).filter(Boolean))]
    .filter(material => !focus.toLocaleLowerCase("zh-CN").includes(material.toLocaleLowerCase("zh-CN")));
  const subject = materials.length > 3 ? `${materials.slice(0, 2).join(" · ")} 等${materials.length}种材料` : materials.join(" · ");
  return { subject, focus, full: [subject, focus].filter(Boolean).join("｜") };
}

function visualTitleHtml(asset, { highlight = true } = {}) {
  const title = visualTitleParts(asset);
  const render = highlight ? highlightSearchText : esc;
  return `${title.subject ? `<span class="visual-title-subject">${render(title.subject)}</span>` : ""}<span class="visual-title-focus">${render(title.focus)}</span>`;
}

function contextChatKey(entity) {
  return `${entity.type}:${entity.id}`;
}

function currentContextConversation() {
  if (!contextChat.entity) return null;
  const key = contextChatKey(contextChat.entity);
  if (!contextChat.conversations.has(key)) contextChat.conversations.set(key, []);
  return contextChat.conversations.get(key);
}

function renderContextChatMessages() {
  const container = document.querySelector("#context-chat-messages");
  const messages = currentContextConversation() || [];
  if (!messages.length) {
    container.innerHTML = `<div class="context-chat-empty"><span>AI</span><p>默认问题已填入下方输入框。你可以直接发送、修改或清空后提出自己的问题。</p></div>`;
    return;
  }
  container.innerHTML = messages.map(message => {
    const evidence = message.role === "assistant" && message.meta
      ? `<div class="context-chat-evidence">${(message.meta.evidence_pages || []).map(page => `<span>PDF第${esc(page)}页</span>`).join("")}${(message.meta.evidence_notes || []).map(value => `<small class="evidence-note">证据：${esc(value)}</small>`).join("")}${(message.meta.limitations || []).map(value => `<small class="limitation">边界：${esc(value)}</small>`).join("")}</div>`
      : "";
    return `<article class="context-chat-message ${esc(message.role)}"><small>${message.role === "user" ? "你" : esc(aiProviderLabel())}</small><p>${esc(message.content)}</p>${evidence}</article>`;
  }).join("");
  container.scrollTop = container.scrollHeight;
}

function openContextChat(type, id, hint = null) {
  const entity = {
    type,
    id: Number(id),
    summary: hint?.summary || (type === "visual" ? "当前图表" : "当前数据"),
    paper: hint?.paper || "",
  };
  const changed = !contextChat.entity || contextChatKey(contextChat.entity) !== contextChatKey(entity);
  contextChat.entity = entity;
  setText("context-chat-entity", entity.summary);
  setText("context-chat-paper", entity.paper || "将读取所属论文的相关 PDF 页面");
  if (changed || !document.querySelector("#context-chat-input").value.trim()) {
    document.querySelector("#context-chat-input").value = defaultContextQuestion;
  }
  renderContextChatMessages();
  window.setTimeout(() => document.querySelector("#context-chat-input").focus(), 120);
}

function closeContextChat() {
  closeVisualAsset();
}

function resetContextChat() {
  if (!contextChat.entity || contextChat.busy) return;
  contextChat.conversations.set(contextChatKey(contextChat.entity), []);
  document.querySelector("#context-chat-input").value = defaultContextQuestion;
  setText("context-chat-status", "已开始新对话 · 基于当前条目与 PDF 相关页回答");
  renderContextChatMessages();
}

async function submitContextChat(event) {
  event.preventDefault();
  if (!contextChat.entity || contextChat.busy) return;
  const input = document.querySelector("#context-chat-input");
  const question = input.value.trim();
  if (!question) {
    toast("请输入问题，或保留默认问题后发送。", true);
    return;
  }
  const conversation = currentContextConversation();
  const history = conversation.map(message => ({ role: message.role, content: message.content }));
  const entity = { ...contextChat.entity };
  const requestGeneration = state.evidenceInspectorRequest;
  const requestPayload = { entity_type: entity.type, entity_id: entity.id, question, history };
  let authorization;
  try {
    authorization = await authorizePreparedAIAction("selected_evidence_chat", "selected_evidence_chat", requestPayload);
  } catch (error) {
    return toast(error.message, true);
  }
  if (!authorization) return toast(`已取消，未向 ${aiProviderLabel()} 发送当前证据。`);
  conversation.push({ role: "user", content: question });
  input.value = "";
  contextChat.busy = true;
  document.querySelector("#context-chat-send").disabled = true;
  document.querySelector("#context-chat").classList.add("thinking");
  setText("context-chat-status", `${aiProviderLabel()} 正在阅读当前证据与 PDF 相关页…`);
  renderContextChatMessages();
  try {
    const result = await globalThis.AutoResearchDesktopPorts.executePreparedAIAction("selected_evidence_chat", authorization.action_id, authorization.consent_nonce);
    if (requestGeneration !== state.evidenceInspectorRequest || !contextChat.entity || contextChatKey(contextChat.entity) !== contextChatKey(entity)) return;
    conversation.push({
      role: "assistant",
      content: result.answer,
      meta: { evidence_pages: result.evidence_pages || [], evidence_notes: result.evidence_notes || [], limitations: result.limitations || [] },
    });
    setText("context-chat-status", `${result.model || aiProviderLabel()} · 参考 PDF 第 ${(result.context_pages || []).join("、") || "相关"} 页`);
  } catch (error) {
    if (requestGeneration !== state.evidenceInspectorRequest) return;
    conversation.push({ role: "assistant", content: `本次回答失败：${error.message}`, meta: { limitations: ["未写入任何科学数据"] } });
    setText("context-chat-status", "回答失败，可修改问题后重试");
  } finally {
    if (requestGeneration !== state.evidenceInspectorRequest) return;
    contextChat.busy = false;
    document.querySelector("#context-chat-send").disabled = false;
    document.querySelector("#context-chat").classList.remove("thinking");
    renderContextChatMessages();
    input.focus();
  }
}

function showEvidenceWorkspace() {
  const dialog = document.querySelector("#visual-dialog");
  const wasDocked = dialog.classList.contains("is-docked");
  const docked = document.body.dataset.view === "search"
    && globalThis.matchMedia?.("(min-width: 1280px)")?.matches;
  dialog.classList.toggle("is-docked", Boolean(docked));
  document.querySelector("#view-search")?.classList.toggle("has-evidence-inspector", Boolean(docked));
  if (docked) {
    dialog.setAttribute("open", "open");
    dialog.setAttribute("aria-modal", "false");
    return;
  }
  if (wasDocked) dialog.removeAttribute("open");
  dialog.setAttribute("aria-modal", "true");
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute("open", "open");
  }
}

function openItemDetail(row, { qualitative = false } = {}) {
  if (!row) return;
  state.detailItem = row;
  state.visualAsset = null;
  document.querySelector("#item-detail-panel").hidden = false;
  document.querySelector("#visual-detail-panel").hidden = true;
  showEvidenceWorkspace();

  const valueText = qualitative ? (row.finding_text || row.value_text || "定性结论") : row.value_text;
  const unit = qualitative ? "" : row.unit;
  document.querySelector("#item-detail-value").innerHTML = qualitative
    ? `<span class="finding-detail-value">实验结论</span>`
    : scientificQuantityHtml(valueText, unit);
  setText("item-detail-meaning", row.meaning || (qualitative ? "论文报告的实验结论" : "数据条目"));
  document.querySelector("#item-detail-badges").innerHTML = `${qualityBadge(row)}<span class="detail-source-badge">${esc(sourceLabel(row))}</span>`;
  setText("item-detail-context", row.context_explanation || "原文未提供更多结构化语境。");
  setText("item-detail-excerpt", row.source_excerpt || row.original_source_excerpt || "当前条目没有可显示的原文片段。");
  const authors = [row.first_author ? `一作：${row.first_author}` : "", row.corresponding_author ? `通讯：${row.corresponding_author}` : ""].filter(Boolean).join(" · ");
  setText("item-detail-paper", [row.article_title, row.doi || "无 DOI", authors].filter(Boolean).join("\n"));
  const page = row.source_page || row.original_source_page;
  setText("item-detail-location", [page ? `PDF 第 ${page} 页` : "页码未记录", row.source_locator || row.original_source_locator].filter(Boolean).join(" · "));
  setText("visual-dialog-type", qualitative ? "QUALITATIVE EVIDENCE" : "DATA EVIDENCE");
  setText("visual-dialog-title", `${row.meaning || "数据条目"} · ${qualitative ? "实验结论" : [row.value_text, row.unit].filter(Boolean).join(" ")}`);

  const linked = row.primary_visual_asset || (row.visual_assets || [])[0];
  const linkedButton = document.querySelector("#item-detail-linked-visual");
  linkedButton.hidden = !linked;
  linkedButton.dataset.visualId = linked ? String(linked.id) : "";
  linkedButton.textContent = linked ? `查看关联${linked.asset_type === "table" ? "表格" : "图片"} · ${linked.label}` : "查看关联图表";
  const sourceButton = document.querySelector("#item-detail-open-source");
  sourceButton.disabled = !row.item_id;
  const editButton = document.querySelector("#item-detail-edit");
  editButton.hidden = isReadOnly();
  editButton.disabled = !row.item_id;
  const pdfLink = document.querySelector("#item-detail-open-pdf");
  pdfLink.href = row.paper_id ? `/api/papers/${row.paper_id}/pdf${page ? `#page=${page}` : ""}` : "#";

  openContextChat("item", Number(row.item_id), {
    summary: qualitative
      ? `${row.meaning || "实验结论"}：${valueText}`
      : `${row.meaning || "数据"}：${row.value_text} ${row.unit || ""}`.trim(),
    paper: row.article_title || "",
  });
}

function itemResultHtml(row) {
  const authors = [row.first_author ? `一作：${row.first_author}` : "", row.corresponding_author ? `通讯：${row.corresponding_author}` : ""].filter(Boolean).join(" · ");
  const reviewLabel = searchReviewLabel(row);
  const reviewButton = isReadOnly() ? "" : `<button class="row-link" data-jump="${row.item_id}">检查/修正</button>`;
  const sourceButton = `<button class="row-link source-link" data-source-search="${row.item_id}">原文证据</button>`;
  const detailButton = `<button class="row-link detail-link" data-item-detail="${row.item_id}">打开条目</button>`;
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
    <div class="result-value-block"><small>报告值</small><div class="value" aria-label="${esc([row.value_text, row.unit].filter(Boolean).join(" "))}">${scientificQuantityHtml(row.value_text, row.unit)}</div><span class="source-kind ${esc(row.source_kind)}">${esc(sourceLabel(row))}</span></div>
    <div class="result-content">
      <div class="result-heading"><div><small>具体意义</small><h3>${highlightSearchText(row.meaning)}</h3>${clusterBadge}${qualityBadge(row)}</div><em class="search-review-state ${esc(row.review_action || row.origin_type)}"${scoreTitle}>${esc(reviewLabel)}</em></div>
      <div class="result-context"><small>实验条件与文章语境</small><p>${highlightSearchText(row.context_explanation)}</p></div>
      ${excerpt ? `<blockquote><span>原文证据</span><p>${highlightSearchText(excerpt)}</p></blockquote>` : ""}
      <footer><div class="result-paper"><strong>${highlightSearchText(row.article_title)}</strong><span>${[row.doi, authors, page ? `PDF 第 ${page} 页` : ""].filter(Boolean).map(esc).join(" · ")}</span></div><div class="result-actions">${detailButton}${visualButton}${sourceButton}${reviewButton}</div></footer>
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
        <div class="result-heading"><div><small>具体意义</small><h3>${highlightSearchText(row.meaning)}</h3><span class="fact-cluster-badge">${esc(merged)}</span>${qualityBadge(row)}</div></div>
        <div class="finding-text"><small>论文报告的结论</small><p>${highlightSearchText(row.finding_text)}</p></div>
        <div class="result-context"><small>实验条件与文章语境</small><p>${highlightSearchText(row.context_explanation)}</p></div>
        ${evidence ? `<blockquote><span>原文证据</span><p>${highlightSearchText(evidence)}</p></blockquote>` : ""}
        <footer><div class="result-paper"><strong>${highlightSearchText(row.article_title)}</strong><span>${[row.doi, page ? `PDF 第 ${page} 页` : ""].filter(Boolean).map(esc).join(" · ")}</span></div><div class="result-actions"><button class="row-link detail-link" data-finding-detail="${row.item_id}">打开条目</button><button class="row-link source-link" data-source-finding="${row.item_id}">原文证据</button></div></footer>
      </div>
    </article>`;
  }).join("");
  el.querySelectorAll("[data-source-finding]").forEach(button => button.addEventListener("click", () => {
    const itemId = Number(button.dataset.sourceFinding);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
  el.querySelectorAll("[data-finding-detail]").forEach(button => button.addEventListener("click", () => {
    const itemId = Number(button.dataset.findingDetail);
    openItemDetail(rows.find(row => Number(row.item_id) === itemId), { qualitative: true });
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
    html.push(`<section class="table-result-group"><header><div><span>${esc(group.asset.label)} · 同一原表</span><h3 class="rich-visual-title">${visualTitleHtml(group.asset)}<small>${group.rows.length} 条匹配数据</small></h3><p>${highlightSearchText(group.asset.context_explanation || group.asset.caption || "表格来源数据")}</p></div><button type="button" data-visual-open="${group.asset.id}">查看完整原表</button></header><div class="table-group-visible">${visible.map(itemResultHtml).join("")}</div>${hidden.length ? `<details><summary>展开其余 ${hidden.length} 条数据</summary><div>${hidden.map(itemResultHtml).join("")}</div></details>` : ""}</section>`);
  });
  el.innerHTML = html.join("");
  el.querySelectorAll("[data-jump]").forEach(btn => btn.addEventListener("click", () => jumpToRow(Number(btn.dataset.jump))));
  el.querySelectorAll("[data-source-search]").forEach(btn => btn.addEventListener("click", () => {
    const itemId = Number(btn.dataset.sourceSearch);
    openSourceViewer(itemId, rows.find(row => Number(row.item_id) === itemId) || null);
  }));
  el.querySelectorAll("[data-visual-open]").forEach(btn => btn.addEventListener("click", () => openVisualAsset(Number(btn.dataset.visualOpen))));
  el.querySelectorAll("[data-item-detail]").forEach(btn => btn.addEventListener("click", () => {
    const row = rows.find(value => Number(value.item_id) === Number(btn.dataset.itemDetail));
    openItemDetail(row);
  }));
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
    const title = visualTitleParts(asset);
    return `<article class="visual-result"><button class="visual-thumb visual-thumb-${esc(asset.asset_type)}" type="button" data-visual-open="${asset.id}" aria-label="查看${esc(title.full)}"><img src="${esc(asset.image_url)}" alt="${esc(title.full)}原文截图" loading="lazy"><span>${esc(typeLabel)}</span></button><div class="visual-result-copy"><div class="visual-result-title"><span>${esc(asset.label)} · PDF第 ${esc(asset.page_start)} 页</span><h3>${visualTitleHtml(asset)}</h3>${qualityBadge(asset)}</div><div class="visual-quantity-list">${tags}</div><p>${highlightSearchText(asset.context_explanation || "待核对原文上下文。")}</p>${factHtml}<small>${esc(asset.article_title)} · ${esc(asset.doi || "无 DOI")}</small><div class="visual-result-actions"><button class="open-visual detail-link" type="button" data-visual-open="${asset.id}">打开详情与 AI 解读</button></div></div></article>`;
  }).join("");
  el.querySelectorAll("[data-visual-open]").forEach(btn => btn.addEventListener("click", () => openVisualAsset(Number(btn.dataset.visualOpen))));
}

function formatVisualVariables(variables) {
  return Object.entries(variables || {}).map(([key, value]) => `${key}：${value}`).join("；");
}

async function openVisualAsset(assetId) {
  state.evidenceInspectorRequest += 1;
  const requestGeneration = state.evidenceInspectorRequest;
  state.detailItem = null;
  document.querySelector("#item-detail-panel").hidden = true;
  document.querySelector("#visual-detail-panel").hidden = false;
  setText("visual-dialog-title", "正在读取完整图表…");
  document.querySelector("#visual-image").removeAttribute("src");
  showEvidenceWorkspace();
  try {
    const asset = await api(`/api/visual-assets/${assetId}`);
    if (requestGeneration !== state.evidenceInspectorRequest) return;
    state.visualAsset = asset;
    setText("visual-dialog-type", asset.asset_type === "table" ? "ORIGINAL TABLE" : "ORIGINAL FIGURE");
    const title = visualTitleParts(asset);
    setText("visual-dialog-title", `${title.full} · ${asset.label} · PDF第 ${asset.page_start} 页`);
    const image = document.querySelector("#visual-image");
    image.src = `${asset.image_url}?ts=${Date.now()}`;
    image.alt = `${title.full}高分辨率原文截图`;
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
    openContextChat("visual", Number(asset.id), {
      summary: title.full,
      paper: asset.article_title || "",
    });
  } catch (error) {
    setText("visual-dialog-title", "图表加载失败");
    setText("visual-context", error.message);
    toast(error.message, true);
  }
}

function invalidateEvidenceInspector() {
  state.evidenceInspectorRequest += 1;
  contextChat.conversations.clear();
  contextChat.busy = false;
  closeVisualAsset();
  const input = document.querySelector("#context-chat-input");
  if (input) input.value = defaultContextQuestion;
  document.querySelector("#context-chat")?.classList.remove("thinking");
  const send = document.querySelector("#context-chat-send");
  if (send) send.disabled = false;
  setText("context-chat-status", "请选择一条证据后开始解读");
  renderContextChatMessages();
}

function closeVisualAsset() {
  state.visualAsset = null;
  state.detailItem = null;
  contextChat.entity = null;
  const dialog = document.querySelector("#visual-dialog");
  document.querySelector("#view-search")?.classList.remove("has-evidence-inspector");
  dialog?.classList.remove("is-docked");
  dialog?.setAttribute("aria-modal", "true");
  if (dialog?.open && typeof dialog.close === "function") dialog.close();
  else dialog?.removeAttribute("open");
}

function showVisualRelatedItems() {
  const asset = state.visualAsset;
  if (!asset) return;
  closeVisualAsset();
  state.searchFilters = { review: "all", source: "all", quality: "all", sort: "relevance" };
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-quality-filter").value = "all";
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

function resetViewportTop() {
  const scrollRoot = document.scrollingElement || document.documentElement;
  if (scrollRoot) {
    scrollRoot.scrollTop = 0;
    scrollRoot.scrollLeft = 0;
  }
  if (document.body && document.body !== scrollRoot) {
    document.body.scrollTop = 0;
    document.body.scrollLeft = 0;
  }
  if (typeof window.scrollTo === "function") {
    try {
      window.scrollTo(0, 0);
    } catch (_error) {
      // Lightweight DOM test environments may expose scrollTo without implementing it.
    }
  }
}

function initializePaperWorkflow() {
  const intake = document.querySelector("#view-upload");
  const mount = document.querySelector("#paper-upload-mount");
  if (!intake || !mount || intake.parentElement !== mount) throw new Error("文献导入界面未静态归入文献工作台");
  if (intake) intake.hidden = false;
  setPaperWorkflowStage(state.paperStage, { refresh: false, resetScroll: false });
}

function setPaperWorkflowStage(stage, options = {}) {
  const next = stage === "upload" ? "upload" : "review";
  state.paperStage = next;
  document.body.dataset.paperStage = next;
  const workspace = document.querySelector("#view-review");
  if (workspace) workspace.dataset.paperStage = next;
  if (next !== "review") setLiteratureInspector(false);
  document.querySelectorAll('.paper-workflow-actions [data-paper-stage]').forEach(button => {
    const active = button.dataset.paperStage === next;
    button.classList.toggle("active", active);
    if (button.getAttribute("role") === "tab") {
      button.setAttribute("aria-selected", active ? "true" : "false");
      button.tabIndex = active ? 0 : -1;
    }
  });
  if (next === "upload" && options.refresh !== false) refreshUploadWorkspace();
  if (options.resetScroll !== false) resetViewportTop();
}

function switchView(name, options = {}) {
  let paperStage = options.paperStage;
  if (name === "upload") {
    name = "paper";
    paperStage = "upload";
  } else if (name === "review") {
    name = "paper";
    paperStage = "review";
  }
  if (isReadOnly() && name !== "search") name = "search";
  if (name !== "paper") {
    literatureWorkflowGeneration += 1;
    setLiteratureStageSummary(null);
    setLiteratureInspector(false);
  }
  if (name !== "search") {
    clearLibrarianPendingSynthesis();
    const inspector = document.querySelector("#visual-dialog");
    if (inspector?.open || inspector?.classList.contains("is-docked")) closeVisualAsset();
  }
  if (name !== "paper" && state.focusReview) setFocusReview(false);
  const viewName = name === "paper" ? "review" : name;
  document.querySelectorAll(".nav,.view").forEach(el => el.classList.remove("active"));
  document.querySelector(`.nav[data-view="${name}"]`)?.classList.add("active");
  document.querySelector(`#view-${viewName}`)?.classList.add("active");
  document.body.dataset.view = name;
  renderViewHeader(name);
  globalThis.AutoResearchWorkbench?.viewChanged?.(name);
  if (name === "paper") setPaperWorkflowStage(paperStage || state.paperStage);
  else resetViewportTop();
  if (name === "search" && !options.skipSearch && !document.querySelector("#search-results").children.length) runSearch();
  if (name === "personal") globalThis.AutoResearchDesktopProduct?.openPersonalImport?.();
  else if (name === "package") globalThis.AutoResearchDesktopProduct?.openPackageCenter?.();
  else globalThis.AutoResearchDesktopProduct?.applySearchUI?.();
}

function setFocusReview(enabled) {
  const active = Boolean(enabled) && document.body.dataset.view === "paper" && state.paperStage === "review" && !isReadOnly();
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
  box.innerHTML = `<div class="upload-result-card ${duplicate ? "duplicate" : "accepted"}"><span>${duplicate ? "DUPLICATE" : "ACCEPTED"}</span><h3>${esc(result.matched_title || result.title || "上传结果")}</h3><p>${esc(result.message)}</p><small>${details}</small><em>上传不会自动调用 DeepSeek。进入当前文章后，由你决定是否开始自动提取与核验。</em><button type="button" data-open-upload-paper="${esc(result.paper_id)}">设为当前文章并继续</button></div>`;
  box.querySelector("[data-open-upload-paper]")?.addEventListener("click", async () => {
    try {
      const switched = await switchCurrentPaper({ paperId: result.paper_id });
      if (!switched) return;
      switchView("paper", { paperStage: "review" });
    } catch (error) {
      toast(`文献已入库，但切换当前文章失败：${error.message}`, true);
    }
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
    [state.papers, state.searchPapers] = await Promise.all([
      api("/api/papers"),
      api("/api/search-papers"),
    ]);
    renderPaperOptions();
    renderSearchScope();
    await refreshUploadWorkspace();
    if (result.outcome !== "rejected" && result.paper_id && !hasUnsavedEdits()) {
      try {
        const switched = await switchCurrentPaper({ paperId: result.paper_id, silent: true });
        if (switched) {
          switchView("paper", { paperStage: "review" });
          toast("文献已设为当前文章。需要处理时，请点击“开始自动提取与核验”；系统不会自动调用 DeepSeek。");
        }
      } catch (switchError) {
        toast(`文献已入库，但切换当前文章失败：${switchError.message}`, true);
      }
    } else {
      toast(result.message, result.outcome === "rejected");
    }
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
  literatureWorkflowGeneration += 1;
  setLiteratureStageSummary(null);
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

const literatureStageKeys = new Set(["schema_version", "job_token", "stage", "paper", "call_count", "max_token_budget", "sending_scope", "possible_charges", "requires_confirmation", "expires_at", "transient", "persistence_allowed"]);
const literatureSendingScopeKeys = new Set(["pdf_page_count", "page_block_count", "branch_count", "focus_count"]);
const literatureCommitKeys = new Set(["schema_version", "status", "paper", "candidate_count", "published_item_count", "existing_item_count", "manual_review_count", "visual_evidence_ready", "idempotent"]);

function hasExactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === keys.size && Object.keys(value).every(key => keys.has(key));
}

function isLiteratureStageSummary(value) {
  const sending = value?.sending_scope;
  const paper = value?.paper;
  return hasExactKeys(value, literatureStageKeys)
    && value.schema_version === "literature-extraction-stage-summary-v1"
    && typeof value.job_token === "string" && value.job_token.length > 0 && value.job_token.length <= 256
    && typeof value.stage === "string" && value.stage.length > 0 && value.stage.length <= 80
    && hasExactKeys(paper, new Set(["title", "doi"])) && typeof paper.title === "string" && (paper.doi === null || typeof paper.doi === "string")
    && Number.isInteger(value.call_count) && value.call_count > 0 && value.call_count <= AI_ACTION_CALL_LIMITS.literature_extraction
    && Number.isInteger(value.max_token_budget) && value.max_token_budget > 0 && value.max_token_budget <= 8_200_000
    && hasExactKeys(sending, literatureSendingScopeKeys)
    && [...literatureSendingScopeKeys].every(key => Number.isInteger(sending[key]) && sending[key] >= 0)
    && value.possible_charges === true && value.requires_confirmation === true
    && value.transient === true && value.persistence_allowed === false;
}

function isLiteratureCommitResult(value) {
  const paper = value?.paper;
  return hasExactKeys(value, literatureCommitKeys)
    && value.schema_version === "literature-extraction-commit-result-v1"
    && value.status === "completed"
    && hasExactKeys(paper, new Set(["title", "doi"])) && typeof paper.title === "string" && (paper.doi === null || typeof paper.doi === "string")
    && ["candidate_count", "published_item_count", "existing_item_count", "manual_review_count"].every(key => Number.isInteger(value[key]) && value[key] >= 0)
    && value.visual_evidence_ready === false
    && typeof value.idempotent === "boolean";
}

function literatureStageLabel(stage) {
  return ({ extraction: "提取候选证据", coverage_gap: "补足证据覆盖", coverage_verification: "核对覆盖范围", adversarial_branches: "双路独立核验", third_review: "第三路复核" })[stage] || "继续证据核验";
}

function setLiteratureStageSummary(summary) {
  const panel = document.querySelector("#literature-ai-stage");
  if (!panel) return;
  panel.hidden = !summary;
  if (!summary) return;
  const sending = summary.sending_scope;
  setText("literature-ai-stage-name", literatureStageLabel(summary.stage));
  setText("literature-ai-stage-calls", String(summary.call_count));
  setText("literature-ai-stage-tokens", Number(summary.max_token_budget).toLocaleString("zh-CN"));
  setText("literature-ai-stage-scope", `${sending.pdf_page_count} 页 PDF · ${sending.page_block_count} 个文本块 · ${sending.focus_count} 个重点区域 · ${sending.branch_count} 路核验`);
}

function literatureContinuationDetail(summary) {
  const sending = summary.sending_scope;
  return `下一阶段：${literatureStageLabel(summary.stage)}\n服务端冻结预算：${summary.call_count} 次调用 / 最多 ${Number(summary.max_token_budget).toLocaleString("zh-CN")} tokens\n发送范围：${sending.pdf_page_count} 页 PDF、${sending.page_block_count} 个文本块、${sending.focus_count} 个重点区域、${sending.branch_count} 路核验`;
}

async function runPreparedLiteratureWorkflow(initialRequest, generation) {
  let domainRequest = initialRequest;
  let confirmationDetail = "";
  while (generation === literatureWorkflowGeneration) {
    const authorization = await authorizePreparedAIAction("literature_extraction", "literature_extraction", domainRequest, confirmationDetail);
    if (!authorization) return { cancelled: true };
    const result = await globalThis.AutoResearchDesktopPorts.executePreparedAIAction("literature_extraction", authorization.action_id, authorization.consent_nonce);
    if (generation !== literatureWorkflowGeneration) return { stale: true };
    if (isLiteratureCommitResult(result)) return { commit: result };
    if (!isLiteratureStageSummary(result)) throw new Error("文献处理返回了无法识别的阶段结果；未显示保存成功。");
    setLiteratureStageSummary(result);
    confirmationDetail = literatureContinuationDetail(result);
    domainRequest = { job_token: result.job_token };
  }
  return { stale: true };
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
  const willCallAI = status.action === 'deepseek_extract'
    || Boolean(forceRescan && status.pdf_ready && status.deepseek_ready);
  const requestPayload = { paper_id: state.paper.id, force_rescan: forceRescan };
  const generation = ++literatureWorkflowGeneration;
  setLiteratureStageSummary(null);
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "处理中…";
  if (state.ai?.configured && state.paper?.pdf_path) {
    updateProgress(2, "自动质量门：准备本地图表证据");
    void pollQualityProgress(state.paper.id);
  } else {
    startProgress("一键提取并核验");
  }
  try {
    const preparedResult = willCallAI
      ? await runPreparedLiteratureWorkflow(requestPayload, generation)
      : await api("/api/current-paper/run-workflow", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestPayload),
        });
    if (willCallAI && (preparedResult.cancelled || preparedResult.stale)) {
      finishProgress(preparedResult.cancelled ? "已取消；没有继续下一阶段，也没有显示保存成功。" : "页面上下文已变化；旧任务结果已忽略。", false);
      if (preparedResult.cancelled) toast(`已取消后续阶段；未再向 ${aiProviderLabel()} 发送论文内容。`);
      return;
    }
    const result = willCallAI ? preparedResult.commit : preparedResult;
    if (generation !== literatureWorkflowGeneration || document.body.dataset.view !== "paper") return;
    await loadCurrentPaper();
    if (willCallAI) {
      setLiteratureStageSummary(null);
      finishProgress(`已安全保存：发布 ${result.published_item_count} 条，已存在 ${result.existing_item_count} 条，人工复核 ${result.manual_review_count} 条。`);
      toast(`文献处理已提交：发布 ${result.published_item_count} 条证据；${result.manual_review_count} 条留待人工复核。`);
      state.searchRequest += 1;
      state.searchResults = [];
      document.querySelector("#search-results")?.replaceChildren();
    } else if (result.action === "prepare_packet") {
      finishProgress("抽取包已生成，等待导入结构化结果。");
      toast(`当前文章已切换，抽取包已生成：${result.action_result?.packet_path || "默认目录"}。`);
    } else if (result.action === "quality_extract" || result.action === "quality_rescan") {
      const summary = result.action_result?.summary || {};
      finishProgress(`完成：双路通过 ${summary.dual_pass_count || 0}，第三次复核通过 ${summary.third_pass_count || 0}，自动拦截 ${summary.manual_review_count || 0}。`);
      toast(`对抗式质量检测完成：自动发布 ${Number(summary.dual_pass_count || 0) + Number(summary.third_pass_count || 0)} 项，自动拦截 ${summary.manual_review_count || 0} 项。`);
    } else {
      const extraction = result.action_result?.extraction || {};
      finishProgress(`完成：新增 ${extraction.inserted ?? 0} 条，已存在 ${extraction.existing ?? 0} 条。`);
      toast(`自动提取完成：新增 ${extraction.inserted ?? 0} 条，已存在 ${extraction.existing ?? 0} 条。`);
    }
  } catch (_error) {
    finishProgress("处理未完成；没有显示或声明保存成功。", false);
    toast("文献处理暂未完成；本地已保存数据不受影响，可稍后重试。", true);
  } finally {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
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
document.querySelectorAll('.paper-workflow-actions [data-paper-stage]').forEach(button => {
  button.addEventListener("click", () => switchView("paper", { paperStage: button.dataset.paperStage }));
  button.addEventListener("keydown", event => handleSearchTabKeydown(event, '.paper-workflow-actions [data-paper-stage]'));
});
document.querySelectorAll("[data-review-object]").forEach(button => {
  button.addEventListener("click", () => setReviewObject(button.dataset.reviewObject));
  button.addEventListener("keydown", event => handleSearchTabKeydown(event, "[data-review-object]"));
});
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
document.querySelector("#literature-inspector-toggle")?.addEventListener("click", event => {
  setLiteratureInspector(event.currentTarget.getAttribute("aria-expanded") !== "true");
});
globalThis.matchMedia?.("(min-width: 1280px)")?.addEventListener?.("change", () => setLiteratureInspector(false));
document.addEventListener("keydown", handleReviewKeyboard);
document.querySelector("#search-form").addEventListener("submit", runSearch);
document.querySelectorAll('[data-search-experience]').forEach(button => {
  button.addEventListener('click', () => setSearchExperience(button.dataset.searchExperience));
  button.addEventListener('keydown', event => handleSearchTabKeydown(event, '[data-search-experience]'));
});
document.querySelector('#librarian-form').addEventListener('submit', submitLibrarian);
document.querySelector('#librarian-synthesis-continue')?.addEventListener('click', () => void continueLibrarianSynthesis());
document.querySelector('#librarian-synthesis-cancel')?.addEventListener('click', () => {
  clearLibrarianPendingSynthesis('已停止，未进行第二次收费调用。');
});
document.querySelector('#librarian-reset').addEventListener('click', resetLibrarian);
document.querySelector('#librarian-history-query').addEventListener('input', event => {
  state.librarianHistoryQuery = event.target.value;
  renderLibrarianHistory();
});
document.querySelectorAll('[data-librarian-result-type]').forEach(button => {
  button.addEventListener('click', () => setLibrarianResultType(button.dataset.librarianResultType));
  button.addEventListener('keydown', event => handleSearchTabKeydown(event, '[data-librarian-result-type]'));
});
document.querySelector('#librarian-input').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    document.querySelector('#librarian-form').requestSubmit();
  }
});
document.querySelectorAll("[data-search-mode]").forEach(button => {
  button.addEventListener("click", () => setSearchMode(button.dataset.searchMode));
  button.addEventListener("keydown", event => handleSearchTabKeydown(event, '[data-search-mode]'));
});
document.querySelector("#search-query").addEventListener("input", scheduleSearchFromInput);
document.querySelector("#search-query").addEventListener("compositionstart", () => { state.searchComposing = true; });
document.querySelector("#search-query").addEventListener("compositionend", () => { state.searchComposing = false; scheduleSearchFromInput(); });
document.querySelector("#search-clear").addEventListener("click", () => {
  document.querySelector("#search-query").value = "";
  document.querySelector("#search-clear").hidden = true;
  document.querySelector("#search-query").focus();
  runSearch(null, { remember: false });
});
document.querySelector("#search-source-filter").addEventListener("change", event => { state.searchFilters.source = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-quality-filter").addEventListener("change", event => { state.searchFilters.quality = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-sort").addEventListener("change", event => { state.searchFilters.sort = event.target.value; runSearch(null, { remember: false }); });
document.querySelector("#search-reset-filters").addEventListener("click", () => {
  state.searchFilters = { review: "all", source: "all", quality: "all", sort: "relevance" };
  document.querySelector("#search-source-filter").value = "all";
  document.querySelector("#search-quality-filter").value = "all";
  document.querySelector("#search-sort").value = "relevance";
  renderActiveFilters();
  runSearch(null, { remember: false });
});
document.querySelectorAll("[data-search-scope]").forEach(button => button.addEventListener("click", () => setSearchScope(button.dataset.searchScope)));
document.querySelector("#search-paper-query").addEventListener("input", event => {
  state.searchPaperQuery = event.target.value;
  renderSearchScope();
});
document.querySelector("#search-paper-select-visible").addEventListener("click", () => {
  state.searchPapers.filter(searchPaperMatches).forEach(paper => state.selectedPaperIds.add(Number(paper.id)));
  renderSearchScope();
  runSearch(null, { remember: false });
});
document.querySelector("#search-paper-clear").addEventListener("click", () => {
  state.selectedPaperIds.clear();
  renderSearchScope();
  runSearch(null, { remember: false });
});
document.addEventListener("keydown", focusSearchShortcut);
document.querySelector("#pdf-upload-form").addEventListener("submit", uploadPdf);
document.querySelector("#pdf-upload-file").addEventListener("change", event => {
  const file = event.target.files[0];
  setText("upload-file-label", file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "点击选择本地 PDF");
});
document.querySelector("#refresh-upload-queue").addEventListener("click", refreshUploadWorkspace);
document.querySelector("#paper-switch-form").addEventListener("submit", submitPaperSwitch);
document.querySelector("#run-current-extraction").addEventListener("click", runCurrentExtraction);
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
document.querySelector("#visual-dialog")?.addEventListener("close", () => {
  state.visualAsset = null;
  state.detailItem = null;
  contextChat.entity = null;
  document.querySelector("#context-chat")?.classList.remove("thinking");
});
globalThis.matchMedia?.("(min-width: 1280px)")?.addEventListener?.("change", () => {
  const inspector = document.querySelector("#visual-dialog");
  if (inspector?.open || inspector?.classList.contains("is-docked")) closeVisualAsset();
});
document.querySelector("#visual-related-items")?.addEventListener("click", showVisualRelatedItems);
document.querySelector("#item-detail-linked-visual")?.addEventListener("click", event => {
  const visualId = Number(event.currentTarget.dataset.visualId);
  if (visualId) openVisualAsset(visualId);
});
document.querySelector("#item-detail-open-source")?.addEventListener("click", () => {
  const row = state.detailItem;
  if (!row?.item_id) return;
  closeVisualAsset();
  openSourceViewer(Number(row.item_id), row);
});
document.querySelector("#item-detail-edit")?.addEventListener("click", () => {
  const itemId = Number(state.detailItem?.item_id);
  if (!itemId || isReadOnly()) return;
  closeVisualAsset();
  jumpToRow(itemId);
});
document.querySelector("#context-chat-form")?.addEventListener("submit", submitContextChat);
document.querySelector("#context-chat-close")?.addEventListener("click", closeContextChat);
document.querySelector("#context-chat-reset")?.addEventListener("click", resetContextChat);
document.querySelector("#context-chat-input")?.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    document.querySelector("#context-chat-form")?.requestSubmit();
  }
});
window.addEventListener("beforeunload", event => {
  if (!hasUnsavedEdits()) return;
  event.preventDefault();
  event.returnValue = "";
});
renderSearchSuggestions();
renderActiveFilters();
async function initializeApplication() {
  initializePaperWorkflow();
  state.uiMode = await api('/api/ui-mode');
  applyUiMode();
  try {
    await loadAIPublicState();
    state.runtimeWarnings.delete("AI 设置");
  } catch (error) {
    state.runtimeWarnings.set("AI 设置", `AI 设置：${error.message}`);
    renderRuntimeWarnings();
  }
  await globalThis.AutoResearchDesktopProduct?.initialize();
  await loadLibrarianHistory();
  setSearchExperience('agent');
  await load();
}
initializeApplication().catch(error => {
  state.runtimeWarnings.set("核心服务", `核心服务：${error.message}`);
  renderRuntimeWarnings();
  setText("workspace-title", "项目服务未完全连接");
  setText("workspace-subtitle", "请确认本地服务正在运行，然后刷新页面。数据库不会因页面加载失败而改变。");
  toast(`页面载入失败：${error.message}`, true);
});
