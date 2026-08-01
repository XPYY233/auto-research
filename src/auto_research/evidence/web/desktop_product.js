(() => {
  const product = {
    available: false,
    searchRepository: "workspace",
    packageStatus: null,
    importing: false,
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

  function el(id) {
    return document.getElementById(id);
  }

  function setPackageNote(message) {
    const note = el("desktop-package-note");
    if (note) note.textContent = message;
  }

  function renderPackageStatus() {
    const status = product.packageStatus || {};
    const active = Boolean(status.active && status.repository_audited);
    el("desktop-package-dot")?.classList.toggle("ready", active);
    if (el("desktop-package-title")) {
      el("desktop-package-title").textContent = active
        ? `${status.package_id} · ${status.package_version}`
        : "尚未导入";
    }
    setPackageNote(active
      ? "已通过签名、哈希与仓库审计，可在本机离线搜索。"
      : "选择我们提供的 .aresearch 资料包即可离线搜索。");
    const officialButton = document.querySelector('[data-search-repository="official"]');
    if (officialButton) officialButton.disabled = !active;
    if (!active && product.searchRepository === "official") setSearchRepository("workspace");
  }

  async function loadPackageStatus() {
    product.packageStatus = await api("/api/desktop/evidence-packages");
    renderPackageStatus();
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
    try {
      await loadPackageStatus();
    } catch (_error) {
      return;
    }
    product.available = true;
    el("desktop-product-panel").hidden = false;
    el("search-repository-switch").hidden = false;
    await loadCredentialStatus();
    applyRepositoryUI();
  }

  function applyRepositoryUI() {
    if (!product.available) return;
    const official = product.searchRepository === "official";
    document.querySelectorAll("[data-search-repository]").forEach(button => {
      const active = button.dataset.searchRepository === product.searchRepository;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelector(".search-scope-selector")?.toggleAttribute("hidden", official);
    el("search-paper-panel")?.toggleAttribute("hidden", official || state.searchScope !== "selected");
    el("search-filter-bar")?.toggleAttribute("hidden", official || state.searchExperience !== "precise");
    const exports = el("search-exports");
    if (exports) exports.hidden = official;
    const help = el("search-help");
    if (help) help.textContent = official
      ? "官方资料库来自已审计的只读资料包；四类结果使用稳定证据身份，不会写入本机工作区。"
      : "检索本机可编辑工作区；支持打开原文、校对、导出与继续提取。";
  }

  function setSearchRepository(repository) {
    if (!product.available || !["workspace", "official"].includes(repository)) return;
    if (repository === "official" && !product.packageStatus?.active) return;
    product.searchRepository = repository;
    applyRepositoryUI();
    runSearch(null, { remember: false });
  }

  function officialTitle(document) {
    return document.display_title || document.meaning_text || document.meaning
      || document.finding_text || document.label || "未命名证据";
  }

  function officialContext(document) {
    return document.source_excerpt || document.caption || document.context_text
      || document.context_explanation || document.evidence_text || "资料包未提供更多原文摘录。";
  }

  function renderOfficialResults(page) {
    const results = page.results || [];
    const container = el("search-results");
    if (!results.length) {
      container.innerHTML = '<div class="blank search-empty"><span>⌕</span><h3>官方资料库中没有直接匹配</h3><p>减少一个条件，或换用材料、元素符号、实验方法和物理量组合。</p></div>';
      return;
    }
    const labels = { item: "数据条目", finding: "实验结论", table: "原始表格", figure: "论文图片" };
    container.innerHTML = results.map(hit => {
      const document = hit.document || {};
      const pageLabel = document.source_page || document.page_start;
      const article = document.article_title || document.paper_title || "未提供文章题目";
      const metadata = [document.doi, pageLabel ? `PDF 第 ${pageLabel} 页` : ""].filter(Boolean).join(" · ");
      const visualUnavailable = document.entity_type === "figure" || document.entity_type === "table";
      return `<article class="official-result-card" data-entity-uid="${esc(document.entity_uid || "")}">
        <header><span>${esc(labels[document.entity_type] || document.entity_type || "证据")}</span><small>官方只读资料包</small></header>
        <div><h3>${esc(officialTitle(document))}</h3><p>${esc(officialContext(document))}</p><strong>${esc(article)}</strong><small>${esc(metadata || document.source_id || "")}</small></div>
        <aside><b>${Number(hit.score || 0)}</b><span>匹配分</span><button type="button" disabled>${visualUnavailable ? "图片未随包提供" : "原文 PDF 未随包提供"}</button></aside>
      </article>`;
    }).join("");
  }

  async function runOfficialSearch(event) {
    event?.preventDefault();
    const query = el("search-query").value.trim();
    const requestId = ++state.searchRequest;
    state.search = query;
    el("search-clear").hidden = !query;
    setSearchBusy(true);
    applyRepositoryUI();
    try {
      const params = new URLSearchParams({
        q: query,
        page: "1",
        page_size: "50",
        entity_type: state.searchMode,
        source_scope: "official",
      });
      const page = await api(`/api/desktop/federated-search?${params}`);
      if (requestId !== state.searchRequest) return;
      const label = { item: "条数据", finding: "条结论", table: "张表格", figure: "幅图片" }[state.searchMode];
      setText("search-summary", query ? `官方资料库 · “${query}” · ${page.total} ${label}` : `官方资料库 · ${page.total} ${label}`);
      renderOfficialResults(page);
    } catch (error) {
      if (requestId !== state.searchRequest) return;
      el("search-results").innerHTML = `<div class="blank search-error"><h3>官方资料库暂时不可用</h3><p>${esc(error.message)}</p></div>`;
      toast(error.message, true);
    } finally {
      if (requestId === state.searchRequest) el("search-results").classList.remove("is-loading");
    }
  }

  function handleSearch(event) {
    if (!product.available || product.searchRepository !== "official") return false;
    void runOfficialSearch(event);
    return true;
  }

  async function waitForJob(jobId) {
    for (let attempt = 0; attempt < 600; attempt += 1) {
      const job = await api(`/api/desktop/evidence-package-jobs/${encodeURIComponent(jobId)}`);
      const label = stageLabels[job.stage] || "正在安全导入";
      setPackageNote(`${label} · ${job.progress}%`);
      if (job.terminal) {
        if (job.stage === "failed") throw new Error(job.error?.message || "资料包导入未完成");
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    throw new Error("资料包导入等待超时，请稍后查看状态。");
  }

  async function importPackage() {
    if (product.importing) return;
    const button = el("desktop-package-import");
    product.importing = true;
    button.disabled = true;
    button.textContent = "等待选择…";
    try {
      if (typeof globalThis.pywebview?.api?.select_evidence_package !== "function") {
        throw new Error("请在 Auto Research 桌面 App 中使用系统文件选择器。");
      }
      const selected = await globalThis.pywebview.api.select_evidence_package();
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
      product.searchRepository = "official";
      applyRepositoryUI();
      setSearchExperience("precise");
      toast("官方资料包已安全导入，可离线搜索。");
    } catch (error) {
      setPackageNote(error.message);
      toast(error.message, true);
    } finally {
      product.importing = false;
      button.disabled = false;
      button.textContent = product.packageStatus?.active ? "更换资料包" : "选择资料包";
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
      toast("AI 密钥已安全保存在当前电脑。")
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteCredential() {
    try {
      await api("/api/desktop/credentials/deepseek", { method: "DELETE" });
      el("desktop-ai-key").value = "";
      await loadCredentialStatus();
      toast("本机 AI 密钥已删除。")
    } catch (error) {
      toast(error.message, true);
    }
  }

  el("desktop-package-import")?.addEventListener("click", importPackage);
  el("desktop-ai-save")?.addEventListener("click", saveCredential);
  el("desktop-ai-delete")?.addEventListener("click", deleteCredential);
  document.querySelectorAll("[data-search-repository]").forEach(button => {
    button.addEventListener("click", () => setSearchRepository(button.dataset.searchRepository));
  });

  globalThis.AutoResearchDesktopProduct = { initialize, handleSearch };
})();
