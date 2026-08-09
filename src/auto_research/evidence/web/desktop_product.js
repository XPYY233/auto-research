(() => {
  const product = {
    available: false,
    searchRepository: "workspace",
    sourceScope: "all",
    packageStatus: null,
    personalSearchStatus: null,
    personalPreview: null,
    personalImportStatus: null,
    importingPackage: false,
    importingPersonal: false,
    suggestingPersonal: false,
    confirmingPersonal: false,
    seriesCounter: 0,
    personalSuggestion: null,
    personalSuggestionRequest: 0,
  };

  const stageLabels = {
    queued: "等待开始",
    snapshot_source: "保护性复制资料包",
    verify_archive: "检查资料包结构",
    verify_signature: "验证官方签名",
    verify_checksums: "核对全部文件",
    extract_staging: "安装到安全暂存区",
    audit_repository: "审计只读资料库",
    activate: "切换活动版本",
    refresh_readiness: "准备离线搜索",
    completed: "导入完成",
    failed: "导入未完成",
  };
  const entityLabels = {
    official: { item: "数据条目", table: "原始表格", figure: "论文图片", finding: "实验结论" },
    private: { item: "测量序列", table: "导入表格", figure: "趋势图 / 实验图像", finding: "我的备注 / 结论" },
  };
  const roleLabels = {
    independent: "自变量",
    dependent: "因变量",
    uncertainty: "误差列",
    condition: "实验条件",
    identifier: "样品 / 记录标识",
    note: "备注",
    ignore: "忽略",
  };

  function el(id) {
    return document.getElementById(id);
  }

  function officialReady() {
    return Boolean(product.packageStatus?.active && product.packageStatus?.repository_audited);
  }

  function privateReady() {
    return product.personalSearchStatus?.ready === true;
  }

  function setPackageNote(message) {
    const note = el("desktop-package-note");
    if (note) note.textContent = message;
  }

  function renderPackageStatus() {
    const status = product.packageStatus || {};
    const active = officialReady();
    el("desktop-package-dot")?.classList.toggle("ready", active);
    if (el("desktop-package-title")) {
      el("desktop-package-title").textContent = active
        ? `${status.package_id} · ${status.package_version}`
        : "尚未导入";
    }
    setPackageNote(active
      ? "已通过签名、哈希与仓库审计，可在本机离线搜索。"
      : "选择我们提供的 .aresearch 资料包即可离线搜索。");
    const button = document.querySelector('[data-source-scope="official"]');
    if (button) button.disabled = !active;
    if (!active && product.sourceScope === "official") {
      product.sourceScope = privateReady() ? "private" : "all";
    }
  }

  function personalStatusCopy(status) {
    const state = status?.state || "not_checked";
    if (state === "ready") return [`${status.document_count || 0} 条记录可搜索`, "已确认数据已安全加入本机私人搜索。"];
    if (state === "empty") return ["尚无已确认数据", "选择实验表格，逐列确认含义和单位后再加入搜索。"];
    if (state === "stale") return ["旧记录仍可搜索", "新确认的数据尚未加入搜索，请重试刷新。"];
    if (state === "retry_required") return ["搜索需要重新准备", "数据已保存，请重试刷新；不要重复确认。"];
    return ["正在检查私人数据", "私人记录不会上传，也不会写入官方资料库。"];
  }

  function renderPersonalSearchStatus() {
    const status = product.personalSearchStatus || { state: "not_checked", ready: false, document_count: 0 };
    const [title, note] = personalStatusCopy(status);
    el("desktop-personal-dot")?.classList.toggle("ready", status.ready === true);
    if (el("desktop-personal-title")) el("desktop-personal-title").textContent = title;
    if (el("desktop-personal-note")) el("desktop-personal-note").textContent = note;
    if (el("personal-search-notice-text")) el("personal-search-notice-text").textContent = note;
    const retry = el("personal-search-refresh");
    if (retry) retry.hidden = !["stale", "retry_required"].includes(status.state);
    const privateButton = document.querySelector('[data-source-scope="private"]');
    if (privateButton) privateButton.classList.toggle("ready", status.ready === true);
  }

  async function loadPackageStatus() {
    product.packageStatus = await api("/api/desktop/evidence-packages");
    renderPackageStatus();
  }

  async function loadPersonalSearchStatus() {
    try {
      product.personalSearchStatus = await api("/api/desktop/personal-imports/search-status");
    } catch (_error) {
      product.personalSearchStatus = { state: "retry_required", ready: false, document_count: 0 };
    }
    renderPersonalSearchStatus();
    renderPackageStatus();
    return product.personalSearchStatus;
  }

  async function loadCredentialStatus() {
    try {
      const status = await api("/api/desktop/credentials/deepseek");
      const title = el("desktop-ai-title");
      if (title) title.textContent = status.configured ? "已安全保存" : "需要时再配置";
      el("desktop-ai-settings")?.classList.toggle("configured", Boolean(status.configured));
      const deleteButton = el("desktop-ai-delete");
      if (deleteButton) deleteButton.disabled = !status.configured;
    } catch (_error) {
      const title = el("desktop-ai-title");
      if (title) title.textContent = "安全存储暂不可用";
    }
  }

  async function initialize() {
    const personalView = el("view-personal");
    const personalPanel = el("personal-import-panel");
    if (personalView && personalPanel && personalPanel.parentElement !== personalView) {
      personalView.appendChild(personalPanel);
    }
    product.available = true;
    el("desktop-product-panel").hidden = false;
    el("search-repository-switch").hidden = false;
    const [packageResult] = await Promise.allSettled([
      loadPackageStatus(),
      loadCredentialStatus(),
      loadPersonalSearchStatus(),
    ]);
    if (packageResult.status === "rejected") {
      product.packageStatus = { unavailable: true };
      renderPackageStatus();
      setPackageNote("官方资料包服务暂不可用；仍可导入和检索本机实验数据。");
    }
    applySearchUI();
  }

  function applySearchUI() {
    if (!product.available) return;
    const offline = product.searchRepository === "offline";
    const personalPage = document.body.dataset.view === "personal";
    document.querySelectorAll("[data-search-repository]").forEach(button => {
      const active = button.dataset.searchRepository === product.searchRepository;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll("[data-source-scope]").forEach(button => {
      const active = button.dataset.sourceScope === product.sourceScope;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    el("offline-source-scope")?.toggleAttribute("hidden", !offline);
    document.querySelector(".search-scope-selector")?.toggleAttribute("hidden", offline);
    el("search-paper-panel")?.toggleAttribute("hidden", offline || state.searchScope !== "selected");
    el("search-filter-bar")?.toggleAttribute("hidden", offline || state.searchExperience !== "precise");
    if (el("search-exports")) {
      el("search-exports").hidden = offline || !["item", "finding"].includes(state.searchMode);
    }
    el("personal-import-panel")?.toggleAttribute("hidden", !personalPage);
    const help = el("search-help");
    if (help) help.textContent = offline
      ? "离线资料库统一检索四类公开记录；官方文献和我的实验保持来源标识，不会互相写入。"
      : "检索本地可编辑文献工作区；支持打开原文、校对、导出与继续提取。";
  }

  function openPersonalImport() {
    if (!product.available) return;
    applySearchUI();
    void loadPersonalSearchStatus();
  }

  function showPrivateSearchResults() {
    product.searchRepository = "offline";
    product.sourceScope = "private";
    switchView("search", { skipSearch: true });
    setSearchExperience("precise");
  }

  function revealSearchWorkspace() {
    const searchView = el("view-search");
    if (typeof searchView?.scrollIntoView === "function") {
      try {
        searchView.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
        return;
      } catch (_error) {
        // Older embedded WebViews use the outer viewport reset below.
      }
    }
    const scrollRoot = document.scrollingElement || document.documentElement;
    if (scrollRoot) {
      scrollRoot.scrollTop = 0;
      scrollRoot.scrollLeft = 0;
    }
  }

  function setSearchRepository(repository, options = {}) {
    if (!product.available || !["workspace", "offline"].includes(repository)) return;
    product.searchRepository = repository;
    applySearchUI();
    revealSearchWorkspace();
    if (options.run !== false) runSearch(null, { remember: false });
  }

  function setSourceScope(scope, options = {}) {
    if (!product.available || !["official", "private", "all"].includes(scope)) return;
    if (scope === "official" && !officialReady()) return;
    product.sourceScope = scope;
    applySearchUI();
    if (options.run !== false) runSearch(null, { remember: false });
  }

  function evidenceTitle(document) {
    return document.display_title || document.meaning_text || document.meaning
      || document.finding_text || document.label || "未命名证据";
  }

  function evidenceContext(document) {
    return document.source_excerpt || document.caption || document.context_text
      || document.context_explanation || document.evidence_text || "当前记录未提供更多文字说明。";
  }

  function conditionsText(conditions) {
    if (!conditions || typeof conditions !== "object" || Array.isArray(conditions)) return "";
    return Object.entries(conditions).map(([key, value]) => `${key}：${value}`).join(" · ");
  }

  function federatedMetadata(document) {
    if (document.source_scope === "private") {
      const primary = [document.project_name, document.sample_name, document.material].filter(Boolean).join(" · ");
      const secondary = [document.method, conditionsText(document.conditions)].filter(Boolean).join(" · ");
      return { primary: primary || "私人实验记录", secondary };
    }
    const page = document.source_page || document.page_start;
    return {
      primary: document.article_title || document.paper_title || "未提供文章题目",
      secondary: [document.doi, page ? `PDF 第 ${page} 页` : ""].filter(Boolean).join(" · "),
    };
  }

  function binaryUnavailableLabel(document) {
    if (document.source_scope === "private") return "当前搜索记录不含原文件";
    return ["figure", "table"].includes(document.entity_type)
      ? "图片未随资料包提供"
      : "原文 PDF 未随资料包提供";
  }

  function renderFederatedResults(page) {
    const results = Array.isArray(page.results) ? page.results : [];
    const container = el("search-results");
    if (!results.length) {
      const scope = { official: "官方文献", private: "我的实验", all: "离线资料库" }[product.sourceScope];
      container.innerHTML = `<div class="blank search-empty"><span>⌕</span><h3>${esc(scope)}中没有直接匹配</h3><p>减少一个条件，或换用材料、元素符号、实验方法和物理量组合。</p></div>`;
      return;
    }
    container.innerHTML = results.map(hit => {
      const document = hit.document || {};
      const scope = document.source_scope === "private" ? "private" : "official";
      const metadata = federatedMetadata(document);
      const typeLabel = entityLabels[scope][document.entity_type] || "证据";
      const sourceLabel = scope === "private" ? "我的实验 · 本机私人" : "官方文献 · 只读";
      return `<article class="federated-result-card source-${scope}" data-source-scope="${esc(document.source_scope || "")}" data-source-id="${esc(document.source_id || "")}" data-entity-uid="${esc(document.entity_uid || "")}">
        <header><span>${esc(typeLabel)}</span><small>${esc(sourceLabel)}</small></header>
        <div class="federated-result-copy"><h3>${esc(evidenceTitle(document))}</h3><p>${esc(evidenceContext(document))}</p><strong>${esc(metadata.primary)}</strong><small>${esc(metadata.secondary)}</small><div class="federated-result-detail" hidden></div></div>
        <aside><b>${Number(hit.score || 0)}</b><span>匹配分</span><button type="button" class="federated-detail-button">查看记录详情</button><button type="button" disabled>${esc(binaryUnavailableLabel(document))}</button></aside>
      </article>`;
    }).join("");
  }

  function scopeCanSearch() {
    if (product.sourceScope === "official") return officialReady();
    if (product.sourceScope === "private") return privateReady();
    return officialReady() || privateReady();
  }

  async function runFederatedSearch(event) {
    event?.preventDefault();
    const query = el("search-query").value.trim();
    const requestId = ++state.searchRequest;
    state.search = query;
    el("search-clear").hidden = !query;
    applySearchUI();
    if (!scopeCanSearch()) {
      const message = product.sourceScope === "official"
        ? "请先导入官方资料包。"
        : product.sourceScope === "private"
          ? "还没有可搜索的私人记录，请先导入并确认实验表格。"
          : "请先导入官方资料包，或确认一份个人实验数据。";
      setText("search-summary", "离线资料库尚未准备好");
      el("search-results").classList.remove("is-loading");
      el("search-results").innerHTML = `<div class="blank search-empty"><span>＋</span><h3>离线资料库尚未准备好</h3><p>${esc(message)}</p></div>`;
      return;
    }
    setSearchBusy(true);
    try {
      const params = new URLSearchParams({
        q: query,
        page: "1",
        page_size: "50",
        entity_type: state.searchMode,
      });
      if (product.sourceScope !== "all") params.set("source_scope", product.sourceScope);
      const page = await api(`/api/desktop/federated-search?${params}`);
      if (requestId !== state.searchRequest) return;
      state.searchResults = page.results || [];
      const scopeLabel = { official: "官方文献", private: "我的实验", all: "全部离线资料" }[product.sourceScope];
      const resultLabel = { item: "条记录", finding: "条结论", table: "张表格", figure: "幅图片" }[state.searchMode];
      setText("search-summary", query
        ? `${scopeLabel} · “${query}” · ${page.total} ${resultLabel}`
        : `${scopeLabel} · ${page.total} ${resultLabel}`);
      renderFederatedResults(page);
    } catch (error) {
      if (requestId !== state.searchRequest) return;
      el("search-results").innerHTML = `<div class="blank search-error"><h3>离线资料库暂时不可用</h3><p>${esc(error.message)}</p></div>`;
      toast(error.message, true);
    } finally {
      if (requestId === state.searchRequest) el("search-results").classList.remove("is-loading");
    }
  }

  function handleSearch(event) {
    if (!product.available || product.searchRepository !== "offline") return false;
    void runFederatedSearch(event);
    return true;
  }

  async function loadFederatedDetail(button) {
    const card = button.closest(".federated-result-card");
    const target = card?.querySelector(".federated-result-detail");
    if (!card || !target || button.disabled) return;
    button.disabled = true;
    button.textContent = "正在读取…";
    try {
      const params = new URLSearchParams({
        source_scope: card.dataset.sourceScope || "",
        source_id: card.dataset.sourceId || "",
        entity_uid: card.dataset.entityUid || "",
      });
      const document = await api(`/api/desktop/federated-evidence?${params}`);
      const metadata = federatedMetadata(document);
      target.innerHTML = `<strong>记录详情</strong><p>${esc(document.meaning_text || evidenceContext(document))}</p><small>${esc([metadata.primary, metadata.secondary, document.value_text, document.unit].filter(Boolean).join(" · "))}</small>`;
      target.hidden = false;
      button.textContent = "已展开详情";
    } catch (error) {
      button.disabled = false;
      button.textContent = "重试查看详情";
      toast(error.message, true);
    }
  }

  async function waitForJob(jobId) {
    for (let attempt = 0; attempt < 600; attempt += 1) {
      const job = await api(`/api/desktop/evidence-package-jobs/${encodeURIComponent(jobId)}`);
      setPackageNote(`${stageLabels[job.stage] || "正在安全导入"} · ${job.progress}%`);
      if (job.terminal) {
        if (job.stage === "failed") throw new Error(job.error?.message || "资料包导入未完成");
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    throw new Error("资料包导入等待超时，请稍后查看状态。");
  }

  async function importPackage() {
    if (product.importingPackage) return;
    const button = el("desktop-package-import");
    product.importingPackage = true;
    button.disabled = true;
    button.textContent = "等待选择…";
    try {
      if (typeof window.pywebview?.api?.select_evidence_package !== "function") {
        throw new Error("请在 Auto Research 桌面 App 中使用系统文件选择器。");
      }
      const selected = await window.pywebview.api.select_evidence_package();
      if (selected?.cancelled) return;
      if (!selected?.ok) throw new Error(selected?.error?.message || "资料包选择失败");
      button.textContent = "正在验证…";
      const job = await api("/api/desktop/evidence-packages/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selection_id: selected.selection.selection_id }),
      });
      await waitForJob(job.job_id);
      await loadPackageStatus();
      product.searchRepository = "offline";
      product.sourceScope = "official";
      setSearchExperience("precise");
      applySearchUI();
      revealSearchWorkspace();
      runSearch(null, { remember: false });
      toast("官方资料包已安全导入，可离线搜索。");
    } catch (error) {
      setPackageNote(error.message);
      toast(error.message, true);
    } finally {
      product.importingPackage = false;
      button.disabled = false;
      button.textContent = officialReady() ? "更换资料包" : "选择资料包";
    }
  }

  function setPersonalProgress(message, isError = false) {
    const progress = el("personal-import-progress");
    if (!progress) return;
    progress.textContent = message;
    progress.classList.toggle("error", isError);
  }

  function selectedSheet() {
    const index = Number(el("personal-import-sheet")?.value || 0);
    return product.personalPreview?.sheets?.[index] || null;
  }

  function roleOptions(selected) {
    return Object.entries(roleLabels).map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`).join("");
  }

  function renderPreviewSheet() {
    const sheet = selectedSheet();
    if (!sheet) return;
    const list = el("personal-import-columns");
    list.innerHTML = (sheet.columns || []).map((column, index) => {
      const samples = (column.sample_values || []).map(value => `<span>${esc(value)}</span>`).join("") || "<span>无样例</span>";
      return `<article class="personal-column-row" data-column-index="${index}" data-source-name="${esc(column.source_name)}">
        <header><strong>${esc(column.source_name)}</strong><small>${esc(column.data_type || "unknown")}</small><div>${samples}</div></header>
        <label>列角色<select data-column-role>${roleOptions(column.role || "ignore")}</select></label>
        <label>具体意义<input data-column-meaning maxlength="500" value="${esc(column.meaning || "")}" placeholder="例如：纳米硬度"></label>
        <label>单位<input data-column-unit maxlength="80" value="${esc(column.unit || "")}" placeholder="无单位可留空"></label>
        <div class="personal-ai-column-note" data-column-ai-note hidden></div>
      </article>`;
    }).join("");
    el("personal-series-list").innerHTML = "";
    product.seriesCounter = 0;
    markPersonalDraftDirty();
  }

  function filenameStem(name) {
    return String(name || "").replace(/\.[^.]+$/, "").trim();
  }

  function setInputValue(id, value, fallback = "") {
    const input = el(id);
    if (input) input.value = value == null ? fallback : String(value);
  }

  function conditionsText(conditions) {
    if (!conditions || typeof conditions !== "object" || Array.isArray(conditions)) return "";
    return Object.entries(conditions).map(([key, value]) => `${key}=${value}`).join("\n");
  }

  function applyLocalPersonalDefaults() {
    const sheet = selectedSheet();
    const stem = filenameStem(product.personalPreview?.source_file?.original_name) || "个人实验数据";
    const sheetName = String(sheet?.sheet_name || "实验数据");
    setInputValue("personal-project-name", stem);
    setInputValue("personal-sample-name", sheetName);
    setInputValue("personal-sample-material", "");
    setInputValue("personal-run-name", sheetName);
    setInputValue("personal-run-method", "未注明");
    setInputValue("personal-run-conditions", "");
    setInputValue("personal-run-note", "");
  }

  function renderPersonalPreview() {
    const preview = product.personalPreview;
    if (!preview) return;
    el("personal-import-workflow").hidden = false;
    const warnings = Array.isArray(preview.warnings) && preview.warnings.length
      ? `提示：${preview.warnings.join("；")}`
      : "未发现额外预览警告";
    el("personal-preview-summary").innerHTML = `<strong>${esc(preview.detected_format?.toUpperCase() || "表格")} · ${preview.sheets?.length || 0} 个工作表</strong><span>${esc(warnings)}</span><small>检测到 ${Number(preview.formula_cell_count || 0)} 个公式单元格；只显示保存值，不会执行公式。</small>`;
    el("personal-import-sheet").innerHTML = (preview.sheets || []).map((sheet, index) => `<option value="${index}">${esc(sheet.sheet_name)} · ${Number(sheet.row_count || 0)} 行 · ${(sheet.columns || []).length} 列</option>`).join("");
    applyLocalPersonalDefaults();
    renderPreviewSheet();
    setPersonalProgress("安全预览完成，正在请 DeepSeek 生成待核验建议…");
    const button = el("personal-reviewed-import");
    if (button) button.disabled = false;
  }

  function availableColumns() {
    return [...document.querySelectorAll(".personal-column-row")].filter(row => row.querySelector("[data-column-role]")?.value !== "ignore").map(row => row.dataset.sourceName);
  }

  function columnOptions(selected, allowEmpty = false) {
    const values = availableColumns();
    const empty = allowEmpty ? '<option value="">无</option>' : '<option value="">请选择</option>';
    return empty + values.map(value => `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(value)}</option>`).join("");
  }

  function refreshSeriesOptions() {
    document.querySelectorAll(".personal-series-row select").forEach(select => {
      const current = select.value;
      const allowEmpty = select.hasAttribute("data-series-uncertainty");
      select.innerHTML = columnOptions(current, allowEmpty);
    });
  }

  function addSeries(initial = null, { quiet = false } = {}) {
    if (availableColumns().length < 2) {
      if (!quiet) toast("至少保留两列非忽略数据，再添加测量序列。", true);
      return null;
    }
    const value = initial && typeof initial === "object" ? initial : {};
    product.seriesCounter += 1;
    const row = document.createElement("article");
    row.className = "personal-series-row";
    row.dataset.seriesNumber = String(product.seriesCounter);
    row.innerHTML = `<label>序列名称<input data-series-name required maxlength="500" value="${esc(value.name || "")}" placeholder="例如：硬度随剂量变化"></label>
      <label>横轴<select data-series-x>${columnOptions(value.x_column || "", false)}</select></label>
      <label>纵轴<select data-series-y>${columnOptions(value.y_column || "", false)}</select></label>
      <label>误差列<select data-series-uncertainty>${columnOptions(value.uncertainty_column || "", true)}</select></label>
      <label class="personal-series-description">说明（可选）<input data-series-description maxlength="1000" value="${esc(value.description || "")}"></label>
      <button type="button" class="personal-remove-series">移除</button>`;
    el("personal-series-list").appendChild(row);
    markPersonalDraftDirty();
    return row;
  }

  function markPersonalDraftDirty() {
    const button = el("personal-reviewed-import");
    if (button && !product.confirmingPersonal) button.disabled = false;
    if (product.personalImportStatus?.import_id && !product.suggestingPersonal) {
      setPersonalProgress("请浏览识别结果；有误时直接修改，确认无误后一次导入。");
    }
  }

  function parseConditions(raw) {
    const conditions = {};
    String(raw || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean).forEach(line => {
      const separator = line.indexOf("=");
      if (separator < 1 || separator === line.length - 1) throw new Error("实验条件请按“名称=数值或说明”逐行填写。");
      const key = line.slice(0, separator).trim();
      const value = line.slice(separator + 1).trim();
      if (!key || !value || Object.prototype.hasOwnProperty.call(conditions, key)) throw new Error("实验条件名称不能为空或重复。");
      conditions[key] = value;
    });
    return conditions;
  }

  function collectDraftPayload() {
    const form = el("personal-import-form");
    if (!form.reportValidity()) throw new Error("请先填写完整的项目、样品和实验信息。");
    const sheetIndex = Number(el("personal-import-sheet").value);
    const columns = [...document.querySelectorAll(".personal-column-row")].map(row => {
      const role = row.querySelector("[data-column-role]").value;
      const meaning = row.querySelector("[data-column-meaning]").value.trim();
      const unit = row.querySelector("[data-column-unit]").value.trim();
      if (role !== "ignore" && !meaning) throw new Error(`请填写“${row.dataset.sourceName}”的具体意义。`);
      return {
        source_name: row.dataset.sourceName,
        role,
        meaning: meaning || null,
        unit: unit || null,
      };
    });
    const allowed = new Set(columns.filter(column => column.role !== "ignore").map(column => column.source_name));
    const series = [...document.querySelectorAll(".personal-series-row")].map((row, index) => {
      const name = row.querySelector("[data-series-name]").value.trim();
      const x = row.querySelector("[data-series-x]").value;
      const y = row.querySelector("[data-series-y]").value;
      const uncertainty = row.querySelector("[data-series-uncertainty]").value;
      if (!name || !x || !y) throw new Error(`请完整填写第 ${index + 1} 个测量序列。`);
      if (![x, y, uncertainty].filter(Boolean).every(value => allowed.has(value))) throw new Error("被忽略的列不能用于测量序列。");
      return {
        series_id: `series-${index + 1}`,
        name,
        x_column: x,
        y_column: y,
        ...(uncertainty ? { uncertainty_column: uncertainty } : {}),
        ...(row.querySelector("[data-series-description]").value.trim() ? { description: row.querySelector("[data-series-description]").value.trim() } : {}),
      };
    });
    const payload = {
      sheet_index: sheetIndex,
      project: { name: el("personal-project-name").value.trim() },
      sample: { name: el("personal-sample-name").value.trim() },
      run: {
        name: el("personal-run-name").value.trim(),
        method: el("personal-run-method").value.trim(),
        conditions: parseConditions(el("personal-run-conditions").value),
      },
      columns,
      series,
    };
    const material = el("personal-sample-material").value.trim();
    const note = el("personal-run-note").value.trim();
    if (material) payload.sample.material = material;
    if (note) payload.run.user_note = note;
    if (Number.isInteger(product.personalImportStatus?.revision)) {
      payload.expected_revision = product.personalImportStatus.revision;
    }
    return payload;
  }

  function applyPersonalSuggestion(suggestion) {
    if (!suggestion || suggestion.schema_version !== "personal-import-suggestion-v1") return;
    product.personalSuggestion = suggestion;
    setInputValue("personal-project-name", suggestion.project?.name, el("personal-project-name")?.value || "个人实验数据");
    setInputValue("personal-sample-name", suggestion.sample?.name, el("personal-sample-name")?.value || "实验样品");
    setInputValue("personal-sample-material", suggestion.sample?.material, "");
    setInputValue("personal-run-name", suggestion.run?.name, el("personal-run-name")?.value || "实验批次");
    setInputValue("personal-run-method", suggestion.run?.method, "未注明");
    setInputValue("personal-run-conditions", conditionsText(suggestion.run?.conditions), "");
    setInputValue("personal-run-note", suggestion.run?.user_note, "");

    const byName = new Map((suggestion.columns || []).map(column => [column.source_name, column]));
    document.querySelectorAll(".personal-column-row").forEach(row => {
      const column = byName.get(row.dataset.sourceName);
      if (!column) return;
      row.querySelector("[data-column-role]").value = column.role || "ignore";
      row.querySelector("[data-column-meaning]").value = column.meaning || "";
      row.querySelector("[data-column-unit]").value = column.unit || "";
      const note = row.querySelector("[data-column-ai-note]");
      const confidence = Number(column.confidence || 0);
      row.classList.toggle("ai-low-confidence", confidence < 0.65);
      if (note) {
        note.hidden = false;
        note.innerHTML = `<strong>${confidence >= 0.8 ? "AI 高置信" : confidence >= 0.65 ? "AI 建议" : "建议重点检查"}</strong><span>${esc(column.rationale || "基于表头和有限样例识别")}</span>`;
      }
    });
    refreshSeriesOptions();
    el("personal-series-list").innerHTML = "";
    product.seriesCounter = 0;
    (suggestion.series || []).forEach(series => addSeries(series, { quiet: true }));
    const warningText = (suggestion.warnings || []).length
      ? `；另有 ${suggestion.warnings.length} 项识别提示，请重点检查低置信字段`
      : "";
    setPersonalProgress(`DeepSeek 已完成预填${warningText}。请浏览一遍，有误时直接修改。`);
    markPersonalDraftDirty();
  }

  async function requestPersonalSuggestion() {
    const importId = product.personalImportStatus?.import_id;
    const sheetIndex = Number(el("personal-import-sheet")?.value || 0);
    if (!importId) return;
    const requestId = ++product.personalSuggestionRequest;
    product.suggestingPersonal = true;
    const retry = el("personal-ai-retry");
    if (retry) retry.hidden = true;
    setPersonalProgress("DeepSeek 正在识别项目、样品、列意义、单位和变量关系…");
    try {
      const suggestion = await api(`/api/desktop/personal-imports/${encodeURIComponent(importId)}/ai-suggestion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sheet_index: sheetIndex, consent: true }),
      });
      if (
        requestId !== product.personalSuggestionRequest
        || importId !== product.personalImportStatus?.import_id
        || sheetIndex !== Number(el("personal-import-sheet")?.value || 0)
      ) return;
      applyPersonalSuggestion(suggestion);
    } catch (error) {
      if (requestId !== product.personalSuggestionRequest) return;
      const fallback = error.code === "personal_ai_not_configured"
        ? "尚未配置 DeepSeek。已保留本地识别结果，你仍可直接检查并导入；如需 AI 预填，请先在右上角配置密钥后重新选择文件。"
        : `${error.message} 已保留本地识别结果，可直接检查并导入。`;
      setPersonalProgress(fallback, error.code !== "personal_ai_not_configured");
      if (retry) retry.hidden = false;
    } finally {
      if (requestId === product.personalSuggestionRequest) product.suggestingPersonal = false;
    }
  }

  async function choosePersonalFile() {
    if (product.importingPersonal) return;
    product.importingPersonal = true;
    setSearchRepository("offline", { run: false });
    setSourceScope("private", { run: false });
    setSearchExperience("precise");
    applySearchUI();
    try {
      if (typeof window.pywebview?.api?.select_personal_data_file !== "function") throw new Error("请在 Auto Research 桌面 App 中使用系统文件选择器。");
      const selected = await window.pywebview.api.select_personal_data_file();
      if (selected?.cancelled) return;
      if (!selected?.ok) throw new Error(selected?.error?.message || "实验数据文件选择失败。");
      setPersonalProgress("正在安全预览表格…");
      const response = await api("/api/desktop/personal-imports/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selection_id: selected.selection.selection_id }),
      });
      product.personalPreview = response.preview;
      product.personalImportStatus = response.status;
      product.personalSuggestion = null;
      renderPersonalPreview();
      revealSearchWorkspace();
      await requestPersonalSuggestion();
    } catch (error) {
      setPersonalProgress(error.message, true);
      toast(error.message, true);
    } finally {
      product.importingPersonal = false;
    }
  }

  async function importReviewedPersonal(event) {
    event.preventDefault();
    const button = el("personal-reviewed-import");
    const importId = product.personalImportStatus?.import_id;
    if (!importId || product.confirmingPersonal || button.disabled) return;
    product.confirmingPersonal = true;
    button.disabled = true;
    try {
      const payload = collectDraftPayload();
      setPersonalProgress("正在保存核验结果并更新“我的实验”搜索…");
      product.personalImportStatus = await api(`/api/desktop/personal-imports/${encodeURIComponent(importId)}/reviewed-import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewed: true, draft: payload }),
      });
      await loadPersonalSearchStatus();
      setPersonalProgress("实验数据已导入，并加入“我的实验”搜索。");
      showPrivateSearchResults();
      toast("实验数据已导入，正在显示“我的实验”搜索结果。");
    } catch (error) {
      if (error.code === "personal_search_refresh_failed") {
        await loadPersonalSearchStatus();
        setPersonalProgress("数据已经保存，但搜索刷新未完成。请点击“重试刷新搜索”，不要重复导入。", true);
      } else {
        setPersonalProgress(error.message, true);
        button.disabled = false;
      }
      toast(error.message, true);
    } finally {
      product.confirmingPersonal = false;
    }
  }

  async function retryPersonalSearch() {
    const button = el("personal-search-refresh");
    button.disabled = true;
    try {
      product.personalSearchStatus = await api("/api/desktop/personal-imports/search-refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      renderPersonalSearchStatus();
      if (privateReady()) {
        setPersonalProgress("私人搜索已刷新，可以检索最新确认数据。");
        showPrivateSearchResults();
        toast("私人搜索已刷新，正在显示最新结果。");
      }
    } catch (error) {
      await loadPersonalSearchStatus();
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function saveCredential() {
    const input = el("desktop-ai-key");
    const apiKey = input.value.trim();
    if (!apiKey) return toast("请先粘贴 API 密钥。", true);
    try {
      await api("/api/desktop/credentials/deepseek", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      input.value = "";
      await loadCredentialStatus();
      toast("AI 密钥已安全保存在当前电脑。");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteCredential() {
    try {
      await api("/api/desktop/credentials/deepseek", { method: "DELETE" });
      el("desktop-ai-key").value = "";
      await loadCredentialStatus();
      toast("本机 AI 密钥已删除。");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function changePersonalSheet() {
    product.personalSuggestion = null;
    applyLocalPersonalDefaults();
    renderPreviewSheet();
    await requestPersonalSuggestion();
  }

  el("desktop-package-import")?.addEventListener("click", importPackage);
  el("desktop-personal-import")?.addEventListener("click", () => switchView("personal"));
  el("personal-import-choose")?.addEventListener("click", choosePersonalFile);
  el("personal-ai-retry")?.addEventListener("click", () => void requestPersonalSuggestion());
  el("personal-import-sheet")?.addEventListener("change", () => void changePersonalSheet());
  el("personal-import-form")?.addEventListener("submit", importReviewedPersonal);
  el("personal-import-form")?.addEventListener("input", markPersonalDraftDirty);
  el("personal-import-form")?.addEventListener("change", markPersonalDraftDirty);
  el("personal-add-series")?.addEventListener("click", () => addSeries());
  el("personal-search-refresh")?.addEventListener("click", retryPersonalSearch);
  el("personal-import-columns")?.addEventListener("change", event => {
    if (event.target.matches("[data-column-role]")) refreshSeriesOptions();
  });
  el("personal-series-list")?.addEventListener("click", event => {
    if (event.target.matches(".personal-remove-series")) {
      event.target.closest(".personal-series-row")?.remove();
      markPersonalDraftDirty();
    }
  });
  el("search-results")?.addEventListener("click", event => {
    const button = event.target.closest(".federated-detail-button");
    if (button) void loadFederatedDetail(button);
  });
  el("desktop-ai-save")?.addEventListener("click", saveCredential);
  el("desktop-ai-delete")?.addEventListener("click", deleteCredential);
  document.querySelectorAll("[data-search-repository]").forEach(button => {
    button.addEventListener("click", () => setSearchRepository(button.dataset.searchRepository));
  });
  document.querySelectorAll("[data-source-scope]").forEach(button => {
    button.addEventListener("click", () => setSourceScope(button.dataset.sourceScope));
  });

  globalThis.AutoResearchDesktopProduct = { initialize, handleSearch, applySearchUI, openPersonalImport };
})();
