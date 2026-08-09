(() => {
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
    plan: "核对导出范围",
    rights_audit: "核对分享权限",
    build_archive: "生成资料包",
    audit_payload: "检查导入内容",
    publish: "写入所选位置",
    completed: "导入完成",
    failed: "导入未完成",
  };

  function create(ports) {
    const required = [
      "api", "toast", "esc", "el", "query", "queryAll",
      "getLiteratureSnapshot", "getOfficialStatus", "officialReady",
      "refreshOfficialStatus", "refreshPrivateStatus", "runOfficialRollback",
      "selectEvidencePackage", "selectExportDestination",
    ];
    if (!ports || required.some(name => typeof ports[name] !== "function")) {
      throw new TypeError("package center ports are incomplete");
    }

    const state = {
      packageCenterStatus: null,
      literaturePlan: null,
      personalExportPlan: null,
      userPackageSelection: null,
      userPackageInspection: null,
      packageJobs: new Map(),
      initialized: false,
    };

    function formatBytes(value) {
      const bytes = Number(value || 0);
      if (!Number.isFinite(bytes) || bytes < 0) return "—";
      if (bytes < 1024) return `${bytes} B`;
      const units = ["KB", "MB", "GB"];
      let amount = bytes;
      let unit = -1;
      do {
        amount /= 1024;
        unit += 1;
      } while (amount >= 1024 && unit < units.length - 1);
      return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
    }

    function installedPackages() {
      const center = state.packageCenterStatus || {};
      const official = center.official || {};
      const current = ports.getOfficialStatus() || {};
      const packages = center.installed_packages || official.installed_versions
        || official.installed_packages || official.installed || current.installed_packages || [];
      return Array.isArray(packages) ? packages : [];
    }

    function renderInstalledPackages() {
      const container = ports.el("package-installed-list");
      if (!container) return;
      const packages = installedPackages();
      const current = ports.getOfficialStatus() || {};
      if (!packages.length) {
        container.innerHTML = `<p class="package-empty">${ports.officialReady() ? "当前版本已启用；已安装版本列表暂不可用。" : "尚未安装官方资料包。"}</p>`;
        return;
      }
      container.innerHTML = packages.map(item => {
        const active = item.active === true
          || (item.package_id === current.package_id && item.package_version === current.package_version);
        const healthy = !item.audit_status || item.audit_status === "audited" || item.audit_status === "ready";
        return `<article class="package-installed-row${active ? " active" : ""}">
          <div><strong>${ports.esc(item.package_id || "官方资料包")} · ${ports.esc(item.package_version || "未知版本")}${active ? " · 当前使用" : ""}</strong><small>${ports.esc(healthy ? (item.content_fingerprint ? `内容指纹 ${String(item.content_fingerprint).slice(0, 12)}` : "已通过本机安装检查") : `审计未通过 · ${item.error_code || "invalid_install"}`)}</small></div>
          <button type="button" data-package-rollback data-package-id="${ports.esc(item.package_id || "")}" data-package-version="${ports.esc(item.package_version || "")}"${healthy ? "" : " disabled"}>回退到此版本</button>
        </article>`;
      }).join("");
    }

    function stageLabel(stage) {
      return stageLabels[stage] || String(stage || "等待开始");
    }

    function rememberJob(job) {
      if (!job?.job_id) return;
      state.packageJobs.set(job.job_id, job);
      renderPackageJobs();
    }

    function renderPackageJobs() {
      const container = ports.el("package-job-list");
      if (!container) return;
      const jobs = [...state.packageJobs.values()].reverse();
      if (!jobs.length) {
        container.innerHTML = '<p class="package-empty">本次还没有资料包任务。</p>';
        return;
      }
      const operationLabels = {
        official_import: "导入官方资料库",
        official_rollback: "回退官方资料库",
        transfer_export: "导出用户资料包",
        transfer_import: "导入用户资料包",
      };
      container.innerHTML = jobs.map(job => {
        const error = job.error || {};
        const outcome = job.outcome || job.result?.outcome || "";
        const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
        return `<article class="package-job-row">
          <div class="package-job-copy"><strong>${ports.esc(operationLabels[job.operation] || "资料包任务")} · ${ports.esc(stageLabel(job.stage))}</strong><small>${ports.esc(job.stage || "queued")} · ${ports.esc(outcome || "处理中")}</small>${error.code ? `<em>${ports.esc(error.message || "任务未完成")}（${ports.esc(error.code)} · ${ports.esc(error.stage || job.stage || "failed")}）</em>` : ""}</div>
          <div class="package-job-progress"><div class="package-job-track"><i style="width:${progress}%"></i></div><b>${progress}%</b></div>
        </article>`;
      }).join("");
    }

    async function waitForJob(initial) {
      let job = initial;
      rememberJob(job);
      for (let attempt = 0; job && !job.terminal && attempt < 600; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 300));
        job = await ports.api(`/api/desktop/package-center/jobs/${encodeURIComponent(job.job_id)}`);
        rememberJob(job);
      }
      if (!job?.terminal) throw new Error("资料包任务等待超时，请稍后查看任务进度。");
      if (job.stage === "failed") {
        const error = new Error(job.error?.message || "资料包任务未完成。");
        error.code = job.error?.code || "package_job_failed";
        throw error;
      }
      return job;
    }

    function literatureScope() {
      return ports.query('input[name="literature-scope"]:checked')?.value || "selected";
    }

    function literatureSelection(scope) {
      const snapshot = ports.getLiteratureSnapshot();
      if (scope === "selected") return [...snapshot.selectedIds];
      if (scope === "filtered") return { ...snapshot.filter };
      return null;
    }

    function updateLiteratureScopeStatus() {
      const target = ports.el("package-literature-scope-status");
      if (!target) return;
      const scope = literatureScope();
      const snapshot = ports.getLiteratureSnapshot();
      if (scope === "selected") {
        const count = snapshot.selectedIds.length;
        target.textContent = count
          ? `已从“搜索数据”读取 ${count} 篇已选论文。`
          : "尚未选择论文；请先在“搜索数据 → 指定多篇文章”中勾选。";
      } else if (scope === "filtered") {
        const count = Object.keys(snapshot.filter).length;
        target.textContent = count
          ? `将使用文献处理页的 ${count} 项当前筛选条件。`
          : "当前没有有效筛选条件；请先筛选文章，或改选“全部论文”。";
      } else {
        target.textContent = `将导出证据库当前登记的全部论文（当前载入 ${Number(snapshot.paperCount || 0)} 篇）。`;
      }
    }

    function planMetrics(plan) {
      return `<span><b>${Number(plan.paper_count || 0)}</b>论文</span><span><b>${Number(plan.item_count || 0)}</b>记录 / 文件</span><span><b>${formatBytes(plan.estimated_bytes)}</b>预计大小</span><span><b>${Number(plan.missing_pdf_count || 0)}</b>缺失 PDF</span><span><b>${Number(plan.rights_requirements?.length || 0)}</b>需逐篇确认</span><span><b>${Number(plan.expires_in_seconds || 0)} 秒</b>计划有效期</span>`;
    }

    function renderLiteraturePlan(plan) {
      state.literaturePlan = plan;
      ports.el("package-literature-plan").hidden = false;
      ports.el("package-literature-metrics").innerHTML = planMetrics(plan);
      const requirements = Array.isArray(plan.rights_requirements) ? plan.rights_requirements : [];
      const rights = ports.el("package-literature-rights");
      rights.innerHTML = requirements.length
        ? `<strong>逐篇确认 PDF 组内分享权限</strong>${requirements.map(item => `<label><input type="checkbox" data-package-rights-paper="${ports.esc(item.paper_uid)}"><span><b>${ports.esc(item.title || item.paper_uid)}</b><br>${ports.esc(item.reason || "请确认具有课题组内部分享权限")}</span></label>`).join("")}`
        : '<p class="package-scope-status">本计划没有需要人工确认的 PDF 权限项。</p>';
      const exportButton = ports.el("package-literature-export");
      exportButton.disabled = plan.exceeds_size_limit === true;
      if (plan.exceeds_size_limit === true) {
        rights.insertAdjacentHTML("beforeend", '<p class="package-scope-status package-size-error">预计内容超过 2 GB，请减少论文后重新生成计划。</p>');
      }
    }

    function renderPersonalExportPlan(plan) {
      state.personalExportPlan = plan;
      ports.el("package-personal-export-plan").hidden = false;
      ports.el("package-personal-metrics").innerHTML = planMetrics(plan);
      ports.el("package-personal-export-plan").querySelector(".package-size-error")?.remove();
      ports.el("package-personal-export").disabled = plan.exceeds_size_limit === true;
      if (plan.exceeds_size_limit === true) {
        ports.el("package-personal-metrics").insertAdjacentHTML("afterend", '<p class="package-scope-status package-size-error">预计内容超过 2 GB，请减少记录后重新生成计划。</p>');
      }
    }

    async function requestExportPlan(kind, scope, selection) {
      return ports.api("/api/desktop/package-center/export-plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, scope, selection }),
      });
    }

    async function planLiteratureExport(event) {
      event.preventDefault();
      const scope = literatureScope();
      const selection = literatureSelection(scope);
      if (scope === "selected" && !selection.length) return ports.toast("请先在搜索页选择至少一篇论文。", true);
      if (scope === "filtered" && !Object.keys(selection).length) return ports.toast("当前没有可用于导出的文章筛选条件。", true);
      try {
        renderLiteraturePlan(await requestExportPlan("literature_collection", scope, selection));
        ports.toast("论文集合导出计划已生成，请核对缺失 PDF 和逐篇权限。 ");
      } catch (error) {
        ports.toast(error.message, true);
      }
    }

    async function planPersonalExport() {
      try {
        renderPersonalExportPlan(await requestExportPlan("personal_experiments", "all", null));
        ports.toast("私人实验导出计划已生成。 ");
      } catch (error) {
        ports.toast(error.message, true);
      }
    }

    function rightsConfirmations() {
      const required = state.literaturePlan?.rights_requirements || [];
      const rights = {};
      for (const item of required) {
        const checkbox = ports.queryAll("[data-package-rights-paper]")
          .find(input => input.dataset.packageRightsPaper === item.paper_uid);
        if (!checkbox?.checked) throw new Error(`请逐篇确认《${item.title || item.paper_uid}》的组内分享权限。`);
        rights[item.paper_uid] = { allowed: true, basis: "用户逐篇确认具有课题组内部分享权限" };
      }
      return rights;
    }

    function exportAcknowledgements(kind) {
      const confirmed = window.confirm(
        "即将导出的用户资料包未加密、来源未认证，且仅限课题组内部使用。请确认你理解这些限制并继续导出。"
      );
      if (!confirmed) return null;
      return {
        unencrypted_ack: true,
        unauthenticated_source_ack: true,
        internal_use_only_ack: true,
        paper_rights: kind === "literature_collection" ? rightsConfirmations() : {},
      };
    }

    async function exportPlannedPackage(kind) {
      const plan = kind === "literature_collection" ? state.literaturePlan : state.personalExportPlan;
      if (!plan?.plan_token) return ports.toast("导出计划不存在或已过期，请重新生成。", true);
      try {
        const acknowledgements = exportAcknowledgements(kind);
        if (acknowledgements === null) return;
        const filename = kind === "literature_collection" ? "Auto-Research-literature.aresearch" : "Auto-Research-personal-experiments.aresearch";
        const selected = await ports.selectExportDestination(filename);
        if (selected?.cancelled) return;
        if (!selected?.ok) throw new Error(selected?.error?.message || "无法选择保存位置。");
        const response = await ports.api("/api/desktop/package-center/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            plan_token: plan.plan_token,
            rights_confirmations: acknowledgements,
            destination_token: selected.destination.destination_token,
          }),
        });
        const completed = await waitForJob(response);
        const checksum = completed.result?.package_sha256 || "";
        ports.toast(checksum ? `导出完成；已同时生成 .sha256 校验文件，易核对码：${checksum.slice(0, 12)}` : "资料包和校验文件已导出。");
      } catch (error) {
        ports.toast(error.message, true);
      }
    }

    function updateUserImportButton() {
      const sha = String(ports.el("package-user-sha")?.value || "").trim().toLowerCase();
      const checks = ports.queryAll(".package-risk-checks input")
        .filter(input => input.id !== "package-keep-conflicts");
      const button = ports.el("package-user-import");
      if (button) button.disabled = !state.userPackageSelection || !/^[0-9a-f]{64}$/.test(sha) || !checks.every(input => input.checked);
    }

    function renderUserInspection(summary) {
      state.userPackageInspection = summary;
      ports.el("package-user-inspection").hidden = false;
      const kindLabel = summary.package_kind === "literature_collection" ? "论文集合包" : "私人实验包";
      ports.el("package-user-summary").innerHTML = `<span><b>${ports.esc(kindLabel)}</b>类型</span><span><b>${ports.esc(summary.package_version || "—")}</b>版本</span><span><b>${Number(summary.file_count || 0)}</b>文件</span><span><b>${formatBytes(summary.total_bytes)}</b>内容大小</span><span><b>${ports.esc(String(summary.package_sha256 || "").slice(0, 12) || "—")}</b>易核对码</span><span><b>SHA-256 only</b>完整性</span><span><b>无</b>加密</span><span><b>未认证</b>来源</span>`;
      ports.el("package-keep-conflicts-row").hidden = summary.package_kind !== "personal_experiments";
      ports.el("package-user-sha").value = "";
      ports.queryAll(".package-risk-checks input").forEach(input => { input.checked = false; });
      updateUserImportButton();
    }

    async function selectUserPackage() {
      try {
        const selected = await ports.selectEvidencePackage();
        if (selected?.cancelled) return;
        if (!selected?.ok) throw new Error(selected?.error?.message || "资料包选择失败。");
        state.userPackageSelection = selected.selection.selection_id;
        const summary = await ports.api("/api/desktop/package-center/inspect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selection_token: state.userPackageSelection }),
        });
        renderUserInspection(summary);
      } catch (error) {
        state.userPackageSelection = null;
        ports.toast(error.message, true);
      }
    }

    async function importUserPackage() {
      const button = ports.el("package-user-import");
      if (button.disabled || !state.userPackageSelection) return;
      button.disabled = true;
      try {
        const response = await ports.api("/api/desktop/package-center/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            selection_token: state.userPackageSelection,
            expected_sha: ports.el("package-user-sha").value.trim().toLowerCase(),
            checksum_ack: ports.el("package-checksum-ack").checked,
            keep_conflicts: ports.el("package-keep-conflicts").checked,
          }),
        });
        const completed = await waitForJob(response);
        state.userPackageSelection = null;
        ports.el("package-user-inspection").hidden = true;
        await ports.refreshPrivateStatus();
        const outcome = completed.outcome || completed.result?.outcome || "";
        ports.toast(outcome === "already_present" ? "该用户资料包已经导入并重新通过检查。" : "用户资料包已导入、激活并可用于搜索。");
      } catch (error) {
        ports.toast(error.message, true);
      } finally {
        updateUserImportButton();
      }
    }

    async function rollbackOfficialPackage(button) {
      if (button.disabled) return;
      button.disabled = true;
      try {
        await ports.runOfficialRollback(
          button.dataset.packageId,
          button.dataset.packageVersion,
        );
        await loadStatus();
        ports.toast("官方资料库已回退到所选版本。 ");
      } catch (error) {
        ports.toast(error.message, true);
      } finally {
        button.disabled = false;
      }
    }

    async function loadStatus() {
      try {
        state.packageCenterStatus = await ports.api("/api/desktop/package-center");
      } catch (_error) {
        state.packageCenterStatus = null;
      }
      renderInstalledPackages();
    }

    function refreshView() {
      updateLiteratureScopeStatus();
      renderInstalledPackages();
    }

    async function open() {
      updateLiteratureScopeStatus();
      await Promise.allSettled([ports.refreshOfficialStatus(), loadStatus()]);
      renderInstalledPackages();
    }

    function init() {
      if (state.initialized) return;
      state.initialized = true;
      const officialMount = ports.el("package-official-status-mount");
      const officialStatus = ports.el("desktop-official-package-status");
      if (officialMount && officialStatus && officialStatus.parentElement !== officialMount) {
        officialMount.appendChild(officialStatus);
      }
      ports.el("package-literature-plan-form")?.addEventListener("submit", planLiteratureExport);
      ports.queryAll('input[name="literature-scope"]').forEach(input => input.addEventListener("change", updateLiteratureScopeStatus));
      ports.el("package-literature-export")?.addEventListener("click", () => void exportPlannedPackage("literature_collection"));
      ports.el("package-personal-plan")?.addEventListener("click", planPersonalExport);
      ports.el("package-personal-export")?.addEventListener("click", () => void exportPlannedPackage("personal_experiments"));
      ports.el("package-user-select")?.addEventListener("click", selectUserPackage);
      ports.el("package-user-sha")?.addEventListener("input", updateUserImportButton);
      ports.queryAll(".package-risk-checks input").forEach(input => input.addEventListener("change", updateUserImportButton));
      ports.el("package-user-import")?.addEventListener("click", importUserPackage);
      ports.el("package-installed-list")?.addEventListener("click", event => {
        const button = event.target.closest("[data-package-rollback]");
        if (button) void rollbackOfficialPackage(button);
      });
      ports.query('.nav[data-view="package"]')?.addEventListener("click", open);
    }

    return Object.freeze({ init, loadStatus, refreshView, open, rememberJob, stageLabel });
  }

  globalThis.AutoResearchPackageCenter = Object.freeze({ create });
})();
