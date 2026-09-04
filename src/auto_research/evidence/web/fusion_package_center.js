(() => {
  "use strict";

  const PACKAGE_STAGE_LABELS = Object.freeze({
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
    completed: "任务完成",
    failed: "任务未完成",
  });
  const PACKAGE_WORKFLOW_TARGETS = Object.freeze({
    official: "#fusion-package-official-select",
    literature: "#fusion-package-literature-plan",
    personal: "#fusion-package-personal-plan",
    dataset: "#fusion-package-dataset",
    userImport: "#fusion-package-user-select",
    jobs: "#fusion-package-jobs",
  });
  const DATASET_TYPES = Object.freeze(["item", "finding", "table", "figure"]);
  const DATASET_SPLITS = Object.freeze(["train", "validation", "test"]);
  // Match DatasetBundleBuilder's bounded papers + evidence, not a UI-sized subset.
  const DATASET_MAX_RECORDS = 100000;
  const DATASET_MAX_PAPERS = 100000;
  const DATASET_RISK_PAGE_SIZE = 50;
  const DATASET_RISK_ID = /^(?:paper_rights:[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}|asset_rights:(?:workspace|official|private):[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}:[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255})$/;
  const DATASET_MISSING_FIELDS = new Set([
    "paper.title", "paper.doi", "paper.year", "item.value_text", "item.meaning",
    "finding.finding_text", "table.caption", "figure.caption",
  ]);
  const ACTIVE_PACKAGE_CURRENT_KEYS = Object.freeze([
    "active", "can_search_offline", "content_fingerprint", "package_id",
    "package_version", "repository_audited",
  ]);
  const INSTALLED_PACKAGE_KEYS = Object.freeze([
    "active", "asset_counts", "audit_status", "content_counts",
    "content_fingerprint", "error_code", "installed_at", "package_id",
    "package_version", "schema",
  ]);
  const OFFICIAL_CONTENT_COUNT_KEYS = Object.freeze([
    "entities", "figures", "findings", "items", "papers", "tables",
  ]);
  const OFFICIAL_ASSET_COUNT_KEYS = Object.freeze(["paper_pdfs", "visual_assets"]);
  const OFFICIAL_PACKAGE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/;
  const OFFICIAL_PACKAGE_VERSION = /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$/;
  const OFFICIAL_FINGERPRINT = /^[a-f0-9]{64}$/;
  const ACTIVITY_RECEIPT_ROOT_KEYS = Object.freeze(["receipts", "revision", "schema_version", "storage"]);
  const ACTIVITY_RECEIPT_KEYS = Object.freeze([
    "activity_type", "artifact_kind", "completed_at", "expires_at", "outcome",
    "receipt_uid", "schema_version", "summary",
  ]);
  const DATASET_RECEIPT_SUMMARY_KEYS = Object.freeze(["archive_size", "checksum_code", "entity_counts", "record_count", "split_counts"]);
  const DATASET_RECEIPT_REQUIRED_KEYS = Object.freeze(["checksum_code", "entity_counts", "record_count", "split_counts"]);
  const TRANSFER_RECEIPT_SUMMARY_KEYS = Object.freeze(["checksum_code", "file_count", "total_bytes"]);
  const RECEIPT_UID = /^[a-f0-9]{64}$/;
  const RECEIPT_CHECKSUM = /^[a-f0-9]{12}$/;
  const RECEIPT_STORAGE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
  const RECEIPT_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
  const PACKAGE_JOB_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{7,159}$/;
  const RECEIPT_RETRY_OPERATIONS = new Set(["transfer_export", "dataset_export"]);

  function createPackageCenterController(ports) {
    if (!ports || typeof ports !== "object") throw new TypeError("package_ports_required");
    const {
      state,
      q,
      qa,
      request,
      safeError,
      cleanText,
      esc,
      setOperation,
      native,
      projectJob,
      openOfficialSearch,
      getCurrentPaperId,
      isActiveView,
    } = ports;
    if (!state || !(state.jobs instanceof Map)) throw new TypeError("package_state_invalid");
    for (const fn of [q, qa, request, safeError, cleanText, esc, setOperation, projectJob, openOfficialSearch, getCurrentPaperId, isActiveView]) {
      if (typeof fn !== "function") throw new TypeError("package_port_invalid");
    }
    if (!native || typeof native.selectEvidencePackage !== "function" || typeof native.selectPackageDestination !== "function" || typeof native.selectDatasetDestination !== "function") {
      throw new TypeError("package_native_ports_invalid");
    }
    let bound = false;
    let datasetPlanGeneration = 0;
    let datasetPlanPending = null;
    const datasetPlanTimeoutMs = 180000;
    let datasetExportPending = false;
    let datasetExportJobId = null;
    const packageJobFamilies = new Map();
    const packageJobPolling = new Set();
    const packageJobInterrupted = new Set();
    const receiptRetryRequests = new Map();
    const receiptRetryPending = new Set();
    const receiptRetryErrors = new Set();
    let receiptLoadPromise = null;
    let receiptForceQueued = false;
    let receiptMutationEpoch = 0;
    const createHistory = globalThis.AutoResearchFusionOperationHistory?.createOperationHistoryController;
    const operationHistory = typeof createHistory === "function" ? createHistory({
      q, qa, request, safeError, cleanText, esc,
      onCountChange(count) { state.operationHistoryCount = count; updatePackageActivityCount(); },
      onReturnToWorkflow(operation) {
        if (operation === "dataset_export") selectPackageWorkflow("dataset");
        else if (operation === "transfer_import") selectPackageWorkflow("userImport");
        else q(".fusion-package-flow")?.scrollIntoView?.({block: "start", behavior: "smooth"});
      },
      async onReceiptStored() { await loadActivityReceipts({force: true}); },
    }) : null;

    function packageNotice(message, kind = "info") {
      const node = q("#fusion-package-status");
      if (node) {
        node.textContent = message;
        node.dataset.kind = kind;
      }
      if (isActiveView()) setOperation(message, kind);
    }

    function formatBytes(value) {
      const bytes = Number(value);
      if (!Number.isFinite(bytes) || bytes < 0) return "—";
      if (bytes < 1024) return `${bytes} B`;
      const units = ["KB", "MB", "GB"];
      let amount = bytes;
      let index = -1;
      do {
        amount /= 1024;
        index += 1;
      } while (amount >= 1024 && index < units.length - 1);
      return `${amount >= 100 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
    }

    function packageMetrics(plan) {
      return `<span><b>${Number(plan?.paper_count || 0)}</b>论文</span>`
        + `<span><b>${Number(plan?.item_count || 0)}</b>记录 / 文件</span>`
        + `<span><b>${formatBytes(plan?.estimated_bytes ?? plan?.total_bytes)}</b>预计大小</span>`
        + `<span><b>${Number(plan?.missing_pdf_count || 0)}</b>缺失 PDF</span>`;
    }

    function selectPackageWorkflow(name, {focus = false} = {}) {
      const normalized = name === "user-import" ? "userImport" : name;
      const targetSelector = PACKAGE_WORKFLOW_TARGETS[normalized];
      if (!targetSelector) return false;
      const panels = Object.entries(PACKAGE_WORKFLOW_TARGETS)
        .map(([key, selector]) => [key, q(selector)?.closest?.("section")])
        .filter(([, panel]) => panel);
      for (const [key, panel] of panels) panel.hidden = key !== normalized;
      qa("[data-package-workflow]").forEach(button => {
        const active = (button.dataset.packageWorkflow === "user-import" ? "userImport" : button.dataset.packageWorkflow) === normalized;
        button.classList.toggle("active", active);
        button.setAttribute("aria-current", active ? "page" : "false");
      });
      if (focus) panels.find(([key]) => key === normalized)?.[1]?.querySelector?.("button,input,select")?.focus?.({preventScroll: true});
      return true;
    }

    function datasetSafePublicText(value, limit = 500) {
      const text = cleanText(value, limit);
      return text && !/[\/\\\x00]/.test(text) ? text : "";
    }

    function publicDatasetPlan(raw, includePrivate) {
      if (raw?.schema_version !== "dataset-export-plan-v1" || raw.include_private !== includePrivate || typeof raw.plan_token !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(raw.plan_token) || raw.binary_assets_included !== false) return null;
      const counts = (value, keys) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return null;
        const result = {};
        for (const key of keys) {
          if (!Number.isSafeInteger(value[key]) || value[key] < 0) return null;
          result[key] = value[key];
        }
        return result;
      };
      const entityCounts = counts(raw.entity_counts, DATASET_TYPES);
      const splitCounts = counts(raw.split_counts, DATASET_SPLITS);
      const recordCount = raw.record_count;
      if (!entityCounts || !splitCounts || !Number.isSafeInteger(recordCount) || recordCount < 0 || recordCount > DATASET_MAX_RECORDS || Object.values(entityCounts).reduce((sum, value) => sum + value, 0) !== recordCount || Object.values(splitCounts).reduce((sum, value) => sum + value, 0) !== recordCount || !raw.missing_fields || typeof raw.missing_fields !== "object" || Array.isArray(raw.missing_fields) || !Array.isArray(raw.rights_risks) || !Number.isSafeInteger(raw.unreviewed_count) || raw.unreviewed_count < 0 || raw.unreviewed_count > recordCount) return null;
      const missing = [];
      for (const [field, value] of Object.entries(raw.missing_fields)) {
        const limit = field.startsWith("paper.") ? DATASET_MAX_PAPERS : entityCounts[field.split(".")[0]];
        if (!DATASET_MISSING_FIELDS.has(field) || !Number.isSafeInteger(value) || value < 0 || value > limit) return null;
        if (value > 0) missing.push([field, value]);
      }
      if (raw.rights_risks.length > DATASET_MAX_PAPERS + recordCount || raw.rights_risks.some(value => typeof value !== "string" || value.length > 543 || !DATASET_RISK_ID.test(value))) return null;
      const risks = [...raw.rights_risks];
      if (new Set(risks).size !== risks.length || raw.rights_ack_required !== (risks.length > 0) || raw.unreviewed_ack_required !== (raw.unreviewed_count > 0)) return null;
      return {
        planToken: raw.plan_token,
        includePrivate,
        entityCounts,
        splitCounts,
        recordCount,
        missing,
        unreviewedCount: raw.unreviewed_count,
        risks,
        rightsAckRequired: raw.rights_ack_required,
        unreviewedAckRequired: raw.unreviewed_ack_required,
        estimatedBytes: Number.isSafeInteger(raw.estimated_bytes) && raw.estimated_bytes >= 0 ? raw.estimated_bytes : null,
      };
    }

    function publicDatasetReceipt(raw) {
      const sha = raw?.archive_sha256;
      if (raw?.schema_version !== "dataset-bundle-v1" || raw.status !== "published" || raw.binary_assets_included !== false || typeof raw.checksum_code !== "string" || !/^[0-9a-f]{12}$/.test(raw.checksum_code) || (sha !== undefined && (typeof sha !== "string" || !/^[0-9a-f]{64}$/.test(sha) || !sha.startsWith(raw.checksum_code))) || !Number.isSafeInteger(raw.record_count) || raw.record_count < 0) return null;
      const fileName = typeof raw.file_name === "string" && /^[^/\\\x00]{1,180}\.zip$/i.test(raw.file_name) ? raw.file_name : "";
      return {
        fileName,
        checksumCode: raw.checksum_code,
        recordCount: raw.record_count,
        archiveSize: Number.isSafeInteger(raw.archive_size) && raw.archive_size >= 0 ? raw.archive_size : null,
      };
    }

    function datasetErrorText(error) {
      const code = /^[a-z0-9_]{3,80}$/.test(String(error?.code || "")) ? String(error.code) : "dataset_operation_failed";
      const message = datasetSafePublicText(error?.message);
      return `${code} · ${message || "数据集操作未完成；现有资料不受影响。"}`;
    }

    function syncDatasetCapability() {
      const available = state.center?.capabilities?.dataset_export === true;
      const button = q("#fusion-dataset-plan-button");
      const label = q("#fusion-dataset-availability");
      if (button) {
        button.disabled = !available || Boolean(datasetPlanPending);
        button.setAttribute?.("aria-busy", String(Boolean(datasetPlanPending)));
      }
      if (label) label.textContent = datasetPlanPending ? `正在本机准备 · 已等待 ${Math.max(0, Math.floor((Date.now() - datasetPlanPending.startedAt) / 1000))} 秒 · 不调用 AI` : available ? "可生成" : "当前不可用";
      return available;
    }

    function datasetPlanReady(plan = state.datasetPlan) {
      const rights = q("#fusion-dataset-rights-ack")?.checked === true;
      const unreviewed = q("#fusion-dataset-unreviewed-ack")?.checked === true;
      return Boolean(plan) && plan.includePrivate === (q("#fusion-dataset-include-private")?.checked === true) && (!plan.rightsAckRequired || rights) && (!plan.unreviewedAckRequired || unreviewed);
    }

    function updateDatasetExportButton() {
      const unresolved = datasetExportJobId && state.jobs.get(datasetExportJobId)?.terminal !== true;
      const ready = !datasetExportPending && !unresolved && datasetPlanReady();
      q("#fusion-dataset-export").disabled = !ready;
      return ready;
    }

    function resetDatasetPlanForScope() {
      datasetPlanGeneration += 1;
      const pending = datasetPlanPending;
      datasetPlanPending = null;
      pending?.cancel(safeError("dataset_plan_cancelled", "数据集范围已变化，请重新生成计划。"));
      syncDatasetCapability();
      state.datasetPlan = null;
      state.datasetReceipt = null;
      q("#fusion-dataset-result").hidden = true;
      q("#fusion-dataset-receipt").hidden = true;
      updateDatasetExportButton();
      packageNotice("数据集范围已变化，已停止等待旧计划；未执行导出。后台可能仍在核对，请手动重新生成计划。", "info");
    }

    function renderDatasetRiskPage(plan, page = 0, focusControl = "") {
      if (state.datasetPlan !== plan) return;
      const target = q("#fusion-dataset-risks");
      if (!plan.risks.length) {
        target.innerHTML = "<p>没有需要额外确认的数据权利风险。</p>";
        return;
      }
      const lastPage = Math.ceil(plan.risks.length / DATASET_RISK_PAGE_SIZE) - 1;
      const current = Math.max(0, Math.min(lastPage, page));
      const start = current * DATASET_RISK_PAGE_SIZE;
      const end = Math.min(start + DATASET_RISK_PAGE_SIZE, plan.risks.length);
      target.innerHTML = `<p role="status">权利风险 ${start + 1}–${end} / ${plan.risks.length} 项；分页仅影响显示，导出仍保留完整风险清单。</p><ul>${plan.risks.slice(start, end).map(value => `<li>${esc(value)}</li>`).join("")}</ul><nav aria-label="权利风险分页"><button type="button" id="fusion-dataset-risks-prev" ${current === 0 ? "disabled" : ""}>上一页</button><button type="button" id="fusion-dataset-risks-next" ${current === lastPage ? "disabled" : ""}>下一页</button></nav>`;
      for (const [id, delta] of [["prev", -1], ["next", 1]]) {
        q(`#fusion-dataset-risks-${id}`)?.addEventListener("click", () => renderDatasetRiskPage(plan, current + delta, id));
      }
      if (focusControl) {
        const id = current === 0 ? "next" : current === lastPage ? "prev" : focusControl;
        q(`#fusion-dataset-risks-${id}`)?.focus?.({preventScroll: true});
      }
    }

    function renderDatasetPlan(plan) {
      state.datasetPlan = plan;
      state.datasetReceipt = null;
      q("#fusion-dataset-receipt").hidden = true;
      q("#fusion-dataset-result").hidden = false;
      const labels = {item: "测量", finding: "结论", table: "表格", figure: "图片", train: "训练", validation: "验证", test: "测试"};
      const metrics = [
        ["总记录", plan.recordCount],
        ...DATASET_TYPES.map(key => [labels[key], plan.entityCounts[key]]),
        ...DATASET_SPLITS.map(key => [labels[key], plan.splitCounts[key]]),
        ["未审核", plan.unreviewedCount],
        ["估算大小", plan.estimatedBytes === null ? "未提供" : formatBytes(plan.estimatedBytes)],
      ];
      q("#fusion-dataset-metrics").innerHTML = metrics.map(([label, value]) => `<span><b>${esc(value)}</b>${esc(label)}</span>`).join("");
      q("#fusion-dataset-missing").innerHTML = plan.missing.length ? plan.missing.map(([field, value]) => `<span><b>${value}</b>${esc(field)}</span>`).join("") : "<p>没有必填字段缺失。</p>";
      renderDatasetRiskPage(plan);
      q("#fusion-dataset-unreviewed-row").hidden = !plan.unreviewedAckRequired;
      q("#fusion-dataset-rights-row").hidden = !plan.rightsAckRequired;
      q("#fusion-dataset-unreviewed-ack").checked = false;
      q("#fusion-dataset-rights-ack").checked = false;
      q("#fusion-dataset-unreviewed-copy").textContent = `我确认本计划包含 ${plan.unreviewedCount} 条未审核记录并继续导出`;
      updateDatasetExportButton();
    }

    async function planDataset(event) {
      event?.preventDefault?.();
      if (datasetPlanPending || datasetExportPending || !syncDatasetCapability()) return;
      const generation = ++datasetPlanGeneration;
      const includePrivate = q("#fusion-dataset-include-private")?.checked === true;
      const isCurrent = () => generation === datasetPlanGeneration && includePrivate === (q("#fusion-dataset-include-private")?.checked === true);
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      let rejectWait;
      const interrupted = new Promise((_resolve, reject) => { rejectWait = reject; });
      const flight = {startedAt: Date.now(), cancel(error) { rejectWait(error); controller?.abort(); }};
      datasetPlanPending = flight;
      syncDatasetCapability();
      const timer = setTimeout(() => flight.cancel(safeError("dataset_plan_timeout", "计划等待已结束；后台可能仍在核对，尚未导出文件。请稍后手动重新生成计划。")), datasetPlanTimeoutMs);
      const elapsedTimer = setInterval(() => { if (datasetPlanPending === flight) syncDatasetCapability(); }, 1000);
      state.datasetPlan = null;
      updateDatasetExportButton();
      q("#fusion-dataset-result").hidden = true;
      q("#fusion-dataset-receipt").hidden = true;
      packageNotice("正在本机准备训练数据集计划，不调用 AI；请查看已等待时间。", "loading");
      try {
        // Bound the entire request, including response JSON, even when transport ignores abort.
        const raw = await Promise.race([request("/api/desktop/package-center/dataset-plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({include_private: includePrivate}), signal: controller?.signal}), interrupted]);
        if (!isCurrent()) return;
        const plan = publicDatasetPlan(raw, includePrivate);
        if (!plan) throw safeError("dataset_plan_invalid", "数据集计划格式无效。");
        renderDatasetPlan(plan);
        packageNotice("数据集计划已生成；请核对划分、缺失字段和风险后选择保存位置。", "success");
      } catch (error) {
        if (!isCurrent()) return;
        packageNotice(datasetErrorText(error), "error");
      } finally {
        clearTimeout(timer);
        clearInterval(elapsedTimer);
        if (datasetPlanPending === flight) {
          datasetPlanPending = null;
          syncDatasetCapability();
          if (!isCurrent()) packageNotice("数据集范围已变化，已停止等待旧计划；未执行导出。后台可能仍在核对，请手动重新生成计划。", "info");
        }
      }
    }

    async function chooseDatasetDestination() {
      const selected = await native.selectDatasetDestination("Auto-Research-dataset.zip");
      if (selected?.cancelled) return null;
      const token = selected?.destination?.destination_token;
      if (selected?.ok !== true || typeof token !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(token)) throw safeError(String(selected?.error?.code || "dataset_destination_invalid"), String(selected?.error?.message || "无法使用所选保存位置。"));
      return token;
    }

    async function exportDataset() {
      const plan = state.datasetPlan;
      if (datasetExportPending || !plan || !updateDatasetExportButton()) return;
      const generation = datasetPlanGeneration, planToken = plan.planToken;
      datasetExportPending = true;
      updateDatasetExportButton();
      try {
        const destinationToken = await chooseDatasetDestination();
        if (!destinationToken) return;
        if (generation !== datasetPlanGeneration || state.datasetPlan !== plan || plan.planToken !== planToken || !datasetPlanReady(plan)) {
          packageNotice("数据集范围或风险确认已变化；未启动导出，请重新核对计划。", "info");
          return;
        }
        packageNotice("正在生成并校验训练数据集…", "loading");
        const initial = await request("/api/desktop/package-center/dataset-export", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({plan_token: plan.planToken, destination_token: destinationToken, rights_acknowledged: q("#fusion-dataset-rights-ack")?.checked === true, unreviewed_acknowledged: q("#fusion-dataset-unreviewed-ack")?.checked === true})});
        if (PACKAGE_JOB_ID.test(String(initial?.job_id || ""))) datasetExportJobId = initial.job_id;
        const completed = await waitPackageJob(initial, "transfer");
        showDatasetCompletion(completed);
      } catch (error) {
        packageNotice(datasetErrorText(error), "error");
      } finally {
        datasetExportPending = false;
        updateDatasetExportButton();
      }
    }

    function showDatasetCompletion(job) {
      const receipt = publicDatasetReceipt(job?.result);
      if (!receipt) throw safeError("dataset_result_invalid", "数据集导出回执无效。");
      state.datasetReceipt = receipt;
      q("#fusion-dataset-receipt").hidden = false;
      q("#fusion-dataset-receipt-name").textContent = receipt.fileName || "所选 ZIP 文件";
      q("#fusion-dataset-receipt-metrics").innerHTML = `<span><b>${receipt.recordCount}</b>记录</span><span><b>${esc(receipt.checksumCode)}</b>校验码</span><span><b>${receipt.archiveSize === null ? "—" : formatBytes(receipt.archiveSize)}</b>文件大小</span>`;
      q("#fusion-dataset-receipt-note").textContent = receipt.fileName ? "文件已写入你选择的位置。" : "文件已写入你选择的位置；当前安全回执未提供文件名或显示文件操作。";
      packageNotice(`训练数据集导出完成 · 校验码 ${receipt.checksumCode}`, "success");
    }

    function packageInstalledVersions() {
      const center = state.center || {};
      const official = center.official || {};
      const current = state.official || {};
      const values = center.installed_packages || official.installed_versions || official.installed_packages || official.installed || current.installed_packages || [];
      return Array.isArray(values) ? values : [];
    }

    function exactObjectKeys(value, keys) {
      return Boolean(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join("|") === [...keys].sort().join("|"));
    }

    function safeReceiptCount(value) {
      return Number.isSafeInteger(value) && value >= 0 ? value : null;
    }

    function safeReceiptTimestamp(value) {
      if (typeof value !== "string" || !RECEIPT_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) return null;
      return value;
    }

    function publicActivityReceipt(raw) {
      if (!exactObjectKeys(raw, ACTIVITY_RECEIPT_KEYS) || raw.schema_version !== "activity-receipt-v1" || !RECEIPT_UID.test(String(raw.receipt_uid || "")) || raw.outcome !== "completed") return null;
      const activityType = raw.activity_type;
      const artifactKind = raw.artifact_kind;
      const validPair = activityType === "dataset_export" ? artifactKind === "dataset_bundle" : activityType === "transfer_export" && ["literature_collection", "personal_experiments"].includes(artifactKind);
      const completedAt = safeReceiptTimestamp(raw.completed_at);
      const expiresAt = safeReceiptTimestamp(raw.expires_at);
      if (!validPair || !completedAt || !expiresAt || Date.parse(expiresAt) <= Date.parse(completedAt)) return null;
      const summary = raw.summary;
      let metrics;
      if (activityType === "dataset_export") {
        const keys = Object.keys(summary || {}).sort();
        if (keys.some(key => !DATASET_RECEIPT_SUMMARY_KEYS.includes(key)) || DATASET_RECEIPT_REQUIRED_KEYS.some(key => !keys.includes(key)) || !RECEIPT_CHECKSUM.test(String(summary.checksum_code || ""))) return null;
        const recordCount = safeReceiptCount(summary.record_count);
        const archiveSize = summary.archive_size === undefined ? null : safeReceiptCount(summary.archive_size);
        const entityCounts = strictOfficialCounts(summary.entity_counts, DATASET_TYPES);
        const splitCounts = strictOfficialCounts(summary.split_counts, DATASET_SPLITS);
        if (recordCount === null || (summary.archive_size !== undefined && archiveSize === null) || !entityCounts || !splitCounts || Object.values(entityCounts).reduce((sum, value) => sum + value, 0) !== recordCount || Object.values(splitCounts).reduce((sum, value) => sum + value, 0) !== recordCount) return null;
        metrics = [["记录", recordCount], ["测量", entityCounts.item], ["结论", entityCounts.finding], ["表格", entityCounts.table], ["图片", entityCounts.figure], ["训练", splitCounts.train], ["验证", splitCounts.validation], ["留出", splitCounts.test]];
        if (archiveSize !== null) metrics.push(["大小", formatBytes(archiveSize)]);
      } else {
        const keys = Object.keys(summary || {}).sort();
        if (!keys.length || keys.some(key => !TRANSFER_RECEIPT_SUMMARY_KEYS.includes(key)) || !keys.includes("checksum_code") || !RECEIPT_CHECKSUM.test(String(summary.checksum_code || ""))) return null;
        const fileCount = summary.file_count === undefined ? null : safeReceiptCount(summary.file_count);
        const totalBytes = summary.total_bytes === undefined ? null : safeReceiptCount(summary.total_bytes);
        if ((summary.file_count !== undefined && fileCount === null) || (summary.total_bytes !== undefined && totalBytes === null)) return null;
        metrics = [];
        if (fileCount !== null) metrics.push(["文件", fileCount]);
        if (totalBytes !== null) metrics.push(["大小", formatBytes(totalBytes)]);
      }
      return Object.freeze({
        receiptUid: raw.receipt_uid,
        activityType,
        artifactKind,
        completedAt,
        checksumCode: raw.summary.checksum_code,
        metrics,
      });
    }

    function publicActivityReceiptSnapshot(raw) {
      if (!exactObjectKeys(raw, ACTIVITY_RECEIPT_ROOT_KEYS) || raw.schema_version !== "activity-receipts-v1" || !Number.isSafeInteger(raw.revision) || raw.revision < 0 || !RECEIPT_STORAGE.test(String(raw.storage || "")) || !Array.isArray(raw.receipts) || raw.receipts.length > 100) return null;
      const receipts = raw.receipts.map(publicActivityReceipt);
      return receipts.every(Boolean) ? Object.freeze({revision: raw.revision, receipts: Object.freeze(receipts)}) : null;
    }

    function receiptTypeLabel(receipt) {
      return {literature_collection: "论文集合", personal_experiments: "私人实验", dataset_bundle: "训练数据集"}[receipt.artifactKind] || "导出任务";
    }

    function receiptCompletedLabel(value) {
      try {
        return new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(value));
      } catch (_error) {
        return "完成时间不可用";
      }
    }

    function updatePackageActivityCount() {
      const node = q("#fusion-package-context-jobs");
      if (node) node.textContent = String(state.jobs.size + (Array.isArray(state.receipts) ? state.receipts.length : 0) + (Number.isSafeInteger(state.operationHistoryCount) ? state.operationHistoryCount : 0));
    }

    function renderActivityReceipts() {
      const host = q("#fusion-package-receipts");
      const status = q("#fusion-package-receipts-status");
      const clear = q("#fusion-package-receipts-clear");
      if (!host || !status || !clear) return;
      const receipts = Array.isArray(state.receipts) ? state.receipts : [];
      const receiptState = state.receiptsStatus || "idle";
      updatePackageActivityCount();
      clear.disabled = receiptState === "loading" || receipts.length === 0;
      if (receiptState === "loading") {
        status.textContent = "正在读取最近完成回执…";
        status.dataset.kind = "loading";
        host.innerHTML = "<p>读取完成后将在此显示。</p>";
        return;
      }
      if (receiptState === "error") {
        status.textContent = "最近完成回执暂时不可用；本次任务与已导出文件不受影响。";
        status.dataset.kind = "error";
        host.innerHTML = "<p>稍后重新进入资料包中心即可再次读取。</p>";
        return;
      }
      status.textContent = receipts.length ? `已读取 ${receipts.length} 条最近完成回执。` : "目前没有最近完成回执。";
      status.dataset.kind = receipts.length ? "success" : "empty";
      host.innerHTML = receipts.length ? receipts.map((receipt, index) => `<article class="fusion-package-receipt"><div><strong>${esc(receiptTypeLabel(receipt))}</strong><small>${esc(receiptCompletedLabel(receipt.completedAt))}</small></div><div class="fusion-package-metrics">${receipt.metrics.map(([label, value]) => `<span><b>${esc(value)}</b>${esc(label)}</span>`).join("")}<span><b>${esc(receipt.checksumCode)}</b>12 位校验码</span></div><button type="button" data-receipt-index="${index}">删除回执</button></article>`).join("") : "<p>完成导出并保存回执后会显示在这里。</p>";
      qa("[data-receipt-index]").forEach(button => button.addEventListener("click", () => {
        const receipt = receipts[Number(button.dataset.receiptIndex)];
        if (receipt) void deleteActivityReceipt(receipt.receiptUid);
      }));
    }

    async function loadActivityReceipts({force = false} = {}) {
      if (receiptLoadPromise) {
        // A force during an in-flight GET requires a subsequent fresh GET.
        if (force) receiptForceQueued = true;
        return receiptLoadPromise;
      }
      if (state.receiptsLoaded && !force) return state.receipts || [];
      state.receiptsLoading = true;
      state.receiptsStatus = "loading";
      renderActivityReceipts();
      receiptLoadPromise = Promise.resolve().then(async () => {
        try {
          do {
            receiptForceQueued = false;
            const mutationEpoch = receiptMutationEpoch;
            try {
              const snapshot = publicActivityReceiptSnapshot(await request("/api/desktop/package-center/receipts"));
              if (!snapshot) throw safeError("activity_receipts_invalid", "活动回执格式无效。");
              const currentRevision = Number.isSafeInteger(state.receiptsRevision) ? state.receiptsRevision : -1;
              if (snapshot.revision < currentRevision || (mutationEpoch !== receiptMutationEpoch && snapshot.revision === currentRevision)) {
                state.receiptsStatus = state.receiptsLoaded ? "ready" : "error";
                continue;
              }
              state.receiptsRevision = snapshot.revision;
              state.receipts = [...snapshot.receipts];
              state.receiptsLoaded = true;
              state.receiptsStatus = "ready";
            } catch (_error) {
              // A late failed GET must not erase a successfully applied mutation.
              if (mutationEpoch === receiptMutationEpoch) {
                state.receiptsLoaded = false;
                state.receiptsStatus = "error";
              }
            }
          } while (receiptForceQueued);
          return state.receipts || [];
        } finally {
          receiptLoadPromise = null;
          state.receiptsLoading = false;
          renderActivityReceipts();
        }
      });
      return receiptLoadPromise;
    }

    async function updateActivityReceipts(body) {
      try {
        const snapshot = publicActivityReceiptSnapshot(await request("/api/desktop/package-center/receipts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}));
        if (!snapshot) throw safeError("activity_receipts_invalid", "活动回执格式无效。");
        receiptMutationEpoch += 1;
        if (!Number.isSafeInteger(state.receiptsRevision) || snapshot.revision >= state.receiptsRevision) {
          state.receiptsRevision = snapshot.revision;
          state.receipts = [...snapshot.receipts];
        }
        state.receiptsLoaded = true;
        state.receiptsStatus = "ready";
        renderActivityReceipts();
        return true;
      } catch (error) {
        if (error?.code === "activity_receipt_revision_conflict") {
          await loadActivityReceipts({force: true});
          const status = q("#fusion-package-receipts-status");
          if (status) {
            status.textContent = state.receiptsStatus === "ready"
              ? "回执列表已在别处更新，现已刷新；请确认后重试。"
              : "回执列表已在别处更新，但刷新暂未完成；请重新读取后再操作。";
            status.dataset.kind = "error";
          }
          return false;
        }
        const status = q("#fusion-package-receipts-status");
        if (status) {
          status.textContent = "回执操作未完成；本次任务与已导出文件不受影响。";
          status.dataset.kind = "error";
        }
        return false;
      }
    }

    async function deleteActivityReceipt(receiptUid) {
      if (!RECEIPT_UID.test(String(receiptUid || "")) || !Number.isSafeInteger(state.receiptsRevision)) return false;
      return updateActivityReceipts({operation: "delete", expected_revision: state.receiptsRevision, receipt_uid: receiptUid});
    }

    async function clearActivityReceipts() {
      if (!Array.isArray(state.receipts) || !state.receipts.length || !Number.isSafeInteger(state.receiptsRevision)) return false;
      if (typeof globalThis.confirm !== "function" || !globalThis.confirm("确认清空最近完成回执？这不会删除已导出的文件，也不会清除本次任务状态。")) return false;
      return updateActivityReceipts({operation: "clear", expected_revision: state.receiptsRevision, confirm_clear: true});
    }

    function canRetryPackageReceipt(job) {
      return Boolean(job && PACKAGE_JOB_ID.test(String(job.job_id || "")) && job.terminal === true && job.stage === "completed" && job.receipt_status === "pending" && RECEIPT_RETRY_OPERATIONS.has(job.operation));
    }

    async function retryPackageReceipt(jobId) {
      const id = String(jobId || "");
      const initial = state.jobs.get(id);
      if (!canRetryPackageReceipt(initial) || receiptRetryPending.has(id)) return false;
      const generation = (receiptRetryRequests.get(id) || 0) + 1;
      receiptRetryRequests.set(id, generation);
      receiptRetryPending.add(id);
      receiptRetryErrors.delete(id);
      renderPackageJobs();
      try {
        const job = await request(`/api/desktop/package-center/jobs/${encodeURIComponent(id)}/receipt-retry`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
        if (receiptRetryRequests.get(id) !== generation) return false;
        const current = state.jobs.get(id);
        if (current !== initial) return false;
        if (!canRetryPackageReceipt(current) || !job || job.job_id !== id || job.operation !== current.operation || job.terminal !== true || job.stage !== "completed" || job.receipt_status !== "stored") throw safeError("package_receipt_retry_invalid", "完成回执恢复结果无效。");
        rememberPackageJob(job, "transfer");
        await loadActivityReceipts({force: true});
        if (receiptRetryRequests.get(id) !== generation) return false;
        packageNotice("完成回执已恢复；不会重复导出文件。", "success");
        return true;
      } catch (_error) {
        if (receiptRetryRequests.get(id) !== generation) return false;
        receiptRetryErrors.add(id);
        packageNotice("文件已导出，请勿重复导出；完成回执暂未恢复，可再次尝试。", "error");
        return false;
      } finally {
        if (receiptRetryRequests.get(id) === generation) receiptRetryPending.delete(id);
        renderPackageJobs();
      }
    }

    function renderPackageJobs() {
      const host = q("#fusion-package-jobs");
      if (!host) return;
      const jobs = [...state.jobs.values()].reverse();
      updatePackageActivityCount();
      host.innerHTML = jobs.length ? jobs.map(job => {
        const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
        const error = job.error || {};
        const receiptStatus = job.receipt_status === "stored" ? " · 完成回执已保存" : job.receipt_status === "pending" ? " · 文件已导出，回执待恢复；请勿重复导出" : "";
        const retry = canRetryPackageReceipt(job) ? `<button type="button" data-package-receipt-retry="${esc(job.job_id)}"${receiptRetryPending.has(String(job.job_id)) ? " disabled" : ""}>${receiptRetryPending.has(String(job.job_id)) ? "正在恢复回执…" : "恢复完成回执"}</button>` : "";
        const retryError = receiptRetryErrors.has(String(job.job_id)) ? " · 回执恢复未完成，可重试；文件已导出，请勿重复导出" : "";
        const interrupted = packageJobInterrupted.has(String(job.job_id));
        const polling = packageJobPolling.has(String(job.job_id));
        const resume = interrupted ? `<button type="button" data-package-job-resume="${esc(job.job_id)}"${polling ? " disabled" : ""}>${polling ? "正在查看原任务…" : "继续查看原任务"}</button>` : "";
        const tracking = interrupted ? " · 状态读取中断，后台任务可能仍在继续；请勿重复导出" : "";
        return `<article class="fusion-package-row"><div><strong>${esc(PACKAGE_STAGE_LABELS[job.stage] || job.stage || "资料包任务")}</strong><small>${esc(job.operation || "")} ${error.code ? `· ${esc(error.message || error.code)}` : ""}${esc(receiptStatus + retryError + tracking)}</small></div><div class="fusion-package-job-progress"><div class="fusion-package-job-track"><i style="width:${progress}%"></i></div><b>${progress}%</b></div>${retry}${resume}</article>`;
      }).join("") : "<p>本次还没有资料包任务。</p>";
      qa("[data-package-receipt-retry]").forEach(button => button.addEventListener("click", () => void retryPackageReceipt(button.dataset.packageReceiptRetry)));
      qa("[data-package-job-resume]").forEach(button => button.addEventListener("click", () => void resumePackageJob(button.dataset.packageJobResume)));
    }

    function rememberPackageJob(job, family = "transfer") {
      if (!job?.job_id) return;
      state.jobs.set(String(job.job_id), job);
      packageJobFamilies.set(String(job.job_id), family);
      if (job.terminal === true) packageJobInterrupted.delete(String(job.job_id));
      projectJob(job, family);
      renderPackageJobs();
    }

    function publicOfficialPackageResult(job) {
      const raw = job?.result?.schema === "package-summary-v1" ? job.result : job?.schema === "package-summary-v1" ? job : null;
      if (!raw || raw.package_kind !== "official_evidence" || raw.trusted_official !== true || !["installed", "activated", "already_active"].includes(raw.outcome)) return null;
      const next = raw.next_action;
      const content = raw.content_counts;
      const assets = raw.asset_counts;
      if (!next || next.view !== "search" || next.search_source !== "official" || !content || typeof content !== "object" || Array.isArray(content) || !assets || typeof assets !== "object" || Array.isArray(assets)) return null;
      const count = (source, keys) => {
        for (const key of keys) {
          const value = source[key];
          if (Number.isSafeInteger(value) && value >= 0) return value;
        }
        return null;
      };
      return {
        packageId: cleanText(raw.package_id, 160),
        packageVersion: cleanText(raw.package_version, 80),
        outcome: raw.outcome,
        nextAction: {view: "search", searchSource: "official"},
        counts: [
          ["论文", count(content, ["paper_count", "papers", "paper"])],
          ["测量", count(content, ["item_count", "items", "item"])],
          ["结论", count(content, ["finding_count", "findings", "finding"])],
          ["表格", count(content, ["table_count", "tables", "table"])],
          ["图片", count(content, ["figure_count", "figures", "figure"])],
          ["PDF", count(assets, ["pdf_count", "paper_pdfs", "pdfs", "pdf"])],
          ["视觉资产", count(assets, ["visual_asset_count", "visual_assets", "visual"])],
        ].filter(([, value]) => value !== null),
      };
    }

    function strictOfficialCounts(value, keys) {
      if (!exactObjectKeys(value, keys)) return null;
      const result = {};
      for (const key of keys) {
        const count = value[key];
        if (!Number.isSafeInteger(count) || count < 0) return null;
        result[key] = count;
      }
      return result;
    }

    function publicActiveOfficialPackageSummary(center = state.center) {
      if (center?.schema !== "package-center-status-v1" || !exactObjectKeys(center.official, ["current", "installed_versions"]) || !exactObjectKeys(center.official.current, ACTIVE_PACKAGE_CURRENT_KEYS) || !Array.isArray(center.official.installed_versions)) return null;
      const current = center.official.current;
      const packageId = String(current.package_id || "");
      const packageVersion = String(current.package_version || "");
      const fingerprint = String(current.content_fingerprint || "");
      if (current.active !== true || current.repository_audited !== true || current.can_search_offline !== true || !OFFICIAL_PACKAGE_ID.test(packageId) || !OFFICIAL_PACKAGE_VERSION.test(packageVersion) || !OFFICIAL_FINGERPRINT.test(fingerprint)) return null;
      const matches = center.official.installed_versions.filter(value => value?.active === true && value?.audit_status === "ready" && value?.package_id === packageId && value?.package_version === packageVersion && value?.content_fingerprint === fingerprint);
      if (matches.length !== 1) return null;
      const installed = matches[0];
      if (!exactObjectKeys(installed, INSTALLED_PACKAGE_KEYS) || installed.schema !== "installed-official-package-v1" || installed.error_code !== null) return null;
      const content = strictOfficialCounts(installed.content_counts, OFFICIAL_CONTENT_COUNT_KEYS);
      if (!content || content.entities !== content.items + content.findings + content.tables + content.figures) return null;
      const assetKeys = installed.asset_counts && typeof installed.asset_counts === "object" && !Array.isArray(installed.asset_counts) ? Object.keys(installed.asset_counts).sort() : null;
      if (!assetKeys || (assetKeys.length !== 0 && assetKeys.join("|") !== OFFICIAL_ASSET_COUNT_KEYS.join("|"))) return null;
      const assets = assetKeys.length ? strictOfficialCounts(installed.asset_counts, OFFICIAL_ASSET_COUNT_KEYS) : {};
      if (!assets) return null;
      const counts = [["论文", content.papers], ["测量", content.items], ["结论", content.findings], ["表格", content.tables], ["图片", content.figures]];
      if (assetKeys.length) counts.push(["PDF", assets.paper_pdfs], ["视觉资产", assets.visual_assets]);
      return {packageId, packageVersion, outcome: "current_active", nextAction: {view: "search", searchSource: "official"}, counts};
    }

    function rememberOfficialPackageResult(job) {
      const result = publicOfficialPackageResult(job);
      if (!result) return false;
      state.lastOfficialResult = result;
      return true;
    }

    function renderOfficialPackageResult() {
      const result = state.lastOfficialResult || publicActiveOfficialPackageSummary();
      const host = q("#fusion-package-official-result");
      const button = q("#fusion-package-open-official-search");
      if (!host || !button) return;
      if (!result) {
        host.hidden = true;
        button.hidden = true;
        return;
      }
      host.hidden = false;
      q("#fusion-package-result-title").textContent = [result.packageId, result.packageVersion].filter(Boolean).join(" · ") || "官方资料库已就绪";
      q("#fusion-package-result-outcome").textContent = {installed: "已安装并启用", activated: "已切换并启用", already_active: "当前版本已启用", current_active: "当前已启用"}[result.outcome];
      q("#fusion-package-result-counts").innerHTML = result.counts.length ? result.counts.map(([label, value]) => `<span><b>${value}</b>${esc(label)}</span>`).join("") : "<span><b>✓</b>签名与完整性检查通过</span>";
      button.hidden = false;
    }

    function renderPackageCenter() {
      const status = state.official || {};
      const ready = status.active === true && status.repository_audited === true;
      const versions = packageInstalledVersions();
      const reportedCount = Number.isInteger(status.document_count) ? status.document_count : Number.isInteger(status.entity_count) ? status.entity_count : null;
      q("#fusion-package-official-state").textContent = ready ? "已审计启用" : "尚未启用";
      q("#fusion-package-official-id").textContent = ready ? cleanText(status.package_id, 160) : "—";
      q("#fusion-package-official-version").textContent = ready ? cleanText(status.package_version, 80) : "—";
      q("#fusion-package-official-count").textContent = ready && reportedCount !== null ? String(reportedCount) : "—";
      q("#fusion-package-context-official").textContent = ready ? cleanText(status.package_version, 40) : "未启用";
      q("#fusion-package-context-installed").textContent = String(versions.length);
      q("#fusion-package-installed").innerHTML = versions.length ? versions.map(item => {
        const active = item.active === true || (item.package_id === status.package_id && item.package_version === status.package_version);
        const healthy = !item.audit_status || ["audited", "ready"].includes(item.audit_status);
        return `<article class="fusion-package-row${active ? " active" : ""}"><div><strong>${esc(item.package_id || "官方资料包")} · ${esc(item.package_version || "未知版本")}${active ? " · 当前使用" : ""}</strong><small>${esc(item.content_fingerprint ? `内容指纹 ${String(item.content_fingerprint).slice(0, 12)}` : healthy ? "已通过本机检查" : "审计未通过")}</small></div><button type="button" data-package-rollback data-package-id="${esc(item.package_id || "")}" data-package-version="${esc(item.package_version || "")}"${healthy && !active ? "" : " disabled"}>回退到此版本</button></article>`;
      }).join("") : "<p>尚无可回退的已安装版本。</p>";
      renderPackageJobs();
      renderOfficialPackageResult();
      syncDatasetCapability();
    }

    async function loadPackageCenter(force = false) {
      if (state.loading) return state;
      if (state.loaded && !force) {
        await loadActivityReceipts();
        return state;
      }
      state.loading = true;
      packageNotice("正在读取资料包状态…", "loading");
      const receiptLoad = loadActivityReceipts({force});
      const historyLoad = operationHistory?.load({force}) || Promise.resolve([]);
      try {
        const [official, center] = await Promise.all([
          request("/api/desktop/evidence-packages"),
          request("/api/desktop/package-center"),
        ]);
        state.official = official;
        state.center = center;
        state.loaded = true;
        renderPackageCenter();
        packageNotice("资料包状态已更新。", "success");
      } catch (_error) {
        packageNotice("资料包状态暂时不可用；现有文献与私人实验不受影响。", "error");
      } finally {
        await Promise.all([receiptLoad, historyLoad]);
        state.loading = false;
      }
      return state;
    }

    async function waitPackageJob(initial, family) {
      let job = initial;
      const id = String(job?.job_id || "");
      if (!PACKAGE_JOB_ID.test(id)) throw safeError("package_job_invalid", "任务身份无效，请检查任务记录；不要重复导出。");
      packageJobPolling.add(id);
      if (job?.job_id && !job.stage) job = {...job, stage: "queued", progress: 0, terminal: false};
      rememberPackageJob(job, family);
      try {
        for (let attempt = 0; !job.terminal && attempt < 600; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 300));
          const base = family === "official" ? "/api/desktop/evidence-package-jobs" : "/api/desktop/package-center/jobs";
          const next = await request(`${base}/${encodeURIComponent(id)}`);
          if (!next || next.job_id !== id || typeof next.terminal !== "boolean" || (job.operation && next.operation !== job.operation)) throw safeError("package_job_invalid", "任务状态不匹配。");
          job = next;
          rememberPackageJob(job, family);
        }
        if (!job.terminal) throw safeError("package_job_timeout", "任务仍未返回最终状态。");
        if (job.stage === "failed") throw safeError(String(job.error?.code || "package_job_failed"), String(job.error?.message || "资料包任务未完成。"));
        if (job.receipt_status === "stored") await loadActivityReceipts({force: true});
        await operationHistory?.load({force: true});
        return job;
      } catch (error) {
        if (job.terminal !== true) {
          packageJobInterrupted.add(id);
          throw safeError("package_job_tracking_interrupted", "任务状态读取中断，后台可能仍在继续；请在任务列表点击“继续查看原任务”，不要重复导出。");
        }
        throw error;
      } finally {
        packageJobPolling.delete(id);
        renderPackageJobs();
      }
    }

    async function resumePackageJob(jobId) {
      const id = String(jobId || ""), initial = state.jobs.get(id);
      if (!PACKAGE_JOB_ID.test(id) || !initial || !packageJobInterrupted.has(id) || packageJobPolling.has(id)) return false;
      try {
        const family = packageJobFamilies.get(id) || "transfer";
        const completed = await waitPackageJob(initial, family);
        if (completed.operation === "dataset_export") showDatasetCompletion(completed);
        else {
          if (family === "official") rememberOfficialPackageResult(completed);
          await loadPackageCenter(true);
          packageNotice("原任务状态已恢复；没有创建新的导出或导入任务。", "success");
        }
        return true;
      } catch (error) {
        packageNotice(datasetErrorText(error), "error");
        return false;
      } finally {
        updateDatasetExportButton();
      }
    }

    async function chooseEvidencePackage() {
      const selected = await native.selectEvidencePackage();
      if (selected?.cancelled) return null;
      if (selected?.ok !== true || typeof selected.selection?.selection_id !== "string") throw safeError("package_selection_invalid", "资料包选择失败。");
      return selected.selection.selection_id;
    }

    async function choosePackageDestination(filename) {
      const selected = await native.selectPackageDestination(filename);
      if (selected?.cancelled) return null;
      if (selected?.ok !== true || typeof selected.destination?.destination_token !== "string") throw safeError("package_destination_invalid", "无法使用所选保存位置。");
      return selected.destination.destination_token;
    }

    async function importOfficialPackage() {
      const button = q("#fusion-package-official-select");
      button.disabled = true;
      try {
        const selectionId = await chooseEvidencePackage();
        if (!selectionId) return;
        packageNotice("正在验证官方资料包…", "loading");
        const initial = await request("/api/desktop/evidence-packages/import", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({selection_id: selectionId})});
        const completed = await waitPackageJob(initial, "official");
        rememberOfficialPackageResult(completed);
        await loadPackageCenter(true);
        packageNotice("官方资料包已通过检查并可用于离线搜索。", "success");
      } catch (_error) {
        packageNotice("官方资料包未启用；原有活动版本保持不变。", "error");
      } finally {
        button.disabled = false;
      }
    }

    async function rollbackOfficialPackage(button) {
      if (button.disabled) return;
      button.disabled = true;
      try {
        const initial = await request("/api/desktop/evidence-packages/rollback", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({package_id: button.dataset.packageId, target_version: button.dataset.packageVersion})});
        await waitPackageJob(initial, "official");
        await loadPackageCenter(true);
        packageNotice("官方资料库已回退到所选版本。", "success");
      } catch (_error) {
        packageNotice("回退未完成；当前活动版本保持不变。", "error");
      } finally {
        button.disabled = false;
      }
    }

    async function requestPackagePlan(kind, scope, selection) {
      return request("/api/desktop/package-center/export-plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({kind, scope, selection})});
    }

    function renderLiteraturePackagePlan(plan) {
      state.literaturePlan = plan;
      q("#fusion-package-literature-result").hidden = false;
      q("#fusion-package-literature-metrics").innerHTML = packageMetrics(plan);
      const requirements = Array.isArray(plan.rights_requirements) ? plan.rights_requirements : [];
      q("#fusion-package-literature-rights").innerHTML = requirements.length ? `<strong>逐篇确认 PDF 课题组内部分享权限</strong>${requirements.map(item => `<label><input type="checkbox" data-package-rights-paper="${esc(item.paper_uid)}"><span><b>${esc(item.title || item.paper_uid)}</b><br>${esc(item.reason || "请确认具有课题组内部分享权限")}</span></label>`).join("")}` : "<p>本计划没有需要人工确认的 PDF 权限项。</p>";
      q("#fusion-package-literature-export").disabled = plan.exceeds_size_limit === true;
      if (plan.exceeds_size_limit === true) q("#fusion-package-literature-rights").insertAdjacentHTML?.("beforeend", "<p>预计内容超过 2 GB，请减少论文后重新生成计划。</p>");
    }

    async function planLiteraturePackage(event) {
      event?.preventDefault?.();
      const scope = q('input[name="fusion-literature-scope"]:checked')?.value || "selected";
      const paperId = getCurrentPaperId();
      const selection = scope === "selected" ? (paperId ? [String(paperId)] : []) : null;
      if (scope === "selected" && !selection.length) return packageNotice("请先选择一篇当前论文。", "error");
      try {
        renderLiteraturePackagePlan(await requestPackagePlan("literature_collection", scope, selection));
        packageNotice("论文集合计划已生成，但尚未创建文件；请核对权限后选择保存位置并导出。", "success");
      } catch (_error) {
        packageNotice("论文集合导出计划未生成。", "error");
      }
    }

    async function planPersonalPackage() {
      try {
        const plan = await requestPackagePlan("personal_experiments", "all", null);
        state.personalPlan = plan;
        q("#fusion-package-personal-result").hidden = false;
        q("#fusion-package-personal-metrics").innerHTML = packageMetrics(plan);
        q("#fusion-package-personal-export").disabled = plan.exceeds_size_limit === true;
        packageNotice(plan.exceeds_size_limit === true ? "私人实验内容超过 2 GB，不能导出。" : "私人实验计划已生成，但尚未创建文件；请确认风险后选择保存位置并导出。", plan.exceeds_size_limit === true ? "error" : "success");
      } catch (_error) {
        packageNotice("私人实验导出计划未生成。", "error");
      }
    }

    function packageRightsConfirmations() {
      const rights = {};
      for (const item of state.literaturePlan?.rights_requirements || []) {
        const checkbox = qa("[data-package-rights-paper]").find(input => input.dataset.packageRightsPaper === item.paper_uid);
        if (!checkbox?.checked) throw safeError("package_rights_unconfirmed", `请确认《${item.title || item.paper_uid}》的课题组内部分享权限。`);
        rights[item.paper_uid] = {allowed: true, basis: "用户逐篇确认具有课题组内部分享权限"};
      }
      return rights;
    }

    async function exportPlannedPackage(kind) {
      const plan = kind === "literature_collection" ? state.literaturePlan : state.personalPlan;
      if (!plan?.plan_token) return packageNotice("导出计划不存在或已过期，请重新生成。", "error");
      try {
        const paperRights = kind === "literature_collection" ? packageRightsConfirmations() : {};
        if (typeof globalThis.confirm !== "function" || !globalThis.confirm("即将导出的用户资料包未加密、来源未认证，且仅限课题组内部使用。确认理解这些限制并继续导出？")) return;
        const filename = kind === "literature_collection" ? "Auto-Research-literature.aresearch" : "Auto-Research-personal-experiments.aresearch";
        const destinationToken = await choosePackageDestination(filename);
        if (!destinationToken) return;
        const initial = await request("/api/desktop/package-center/export", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({plan_token: plan.plan_token, rights_confirmations: {unencrypted_ack: true, unauthenticated_source_ack: true, internal_use_only_ack: true, paper_rights: paperRights}, destination_token: destinationToken})});
        const completed = await waitPackageJob(initial, "transfer");
        const checksum = cleanText(completed.result?.package_sha256, 64);
        packageNotice(checksum ? `导出完成；已生成 .sha256 校验文件，易核对码 ${checksum.slice(0, 12)}。` : "资料包与校验文件已导出。", "success");
      } catch (error) {
        packageNotice(error.code === "package_rights_unconfirmed" ? error.message : "资料包未导出；可以核对后重试。", "error");
      }
    }

    function updateUserPackageImportButton() {
      const sha = String(q("#fusion-package-user-sha")?.value || "").trim().toLowerCase();
      const checks = ["#fusion-package-checksum-ack", "#fusion-package-unencrypted-ack", "#fusion-package-source-ack"].every(selector => q(selector)?.checked === true);
      q("#fusion-package-user-import").disabled = !(state.userSelection && /^[0-9a-f]{64}$/.test(sha) && checks);
    }

    async function inspectUserPackage() {
      try {
        const selectionToken = await chooseEvidencePackage();
        if (!selectionToken) return;
        state.userSelection = selectionToken;
        const summary = await request("/api/desktop/package-center/inspect", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({selection_token: selectionToken})});
        state.userInspection = summary;
        q("#fusion-package-user-inspection").hidden = false;
        q("#fusion-package-user-summary").innerHTML = `<span><b>${esc(summary.package_kind === "literature_collection" ? "论文集合包" : "私人实验包")}</b>类型</span><span><b>${esc(summary.package_version || "—")}</b>版本</span><span><b>${Number(summary.file_count || 0)}</b>文件</span><span><b>${formatBytes(summary.total_bytes)}</b>内容大小</span><span><b>${esc(String(summary.package_sha256 || "").slice(0, 12) || "—")}</b>易核对码</span>`;
        q("#fusion-package-keep-conflicts-row").hidden = summary.package_kind !== "personal_experiments";
        q("#fusion-package-user-sha").value = "";
        for (const selector of ["#fusion-package-checksum-ack", "#fusion-package-unencrypted-ack", "#fusion-package-source-ack", "#fusion-package-keep-conflicts"]) q(selector).checked = false;
        updateUserPackageImportButton();
        packageNotice("资料包结构检查完成；请通过外部渠道核对完整 SHA-256。", "success");
      } catch (_error) {
        state.userSelection = null;
        packageNotice("用户资料包检查未完成。", "error");
      }
    }

    async function importUserPackage() {
      const button = q("#fusion-package-user-import");
      if (button.disabled || !state.userSelection) return;
      button.disabled = true;
      try {
        const initial = await request("/api/desktop/package-center/import", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({selection_token: state.userSelection, expected_sha: q("#fusion-package-user-sha").value.trim().toLowerCase(), checksum_ack: true, keep_conflicts: q("#fusion-package-keep-conflicts").checked === true})});
        await waitPackageJob(initial, "transfer");
        state.userSelection = null;
        q("#fusion-package-user-inspection").hidden = true;
        await loadPackageCenter(true);
        packageNotice("用户资料包已通过检查并导入。", "success");
      } catch (_error) {
        packageNotice("用户资料包未导入；现有资料库保持不变。", "error");
      } finally {
        updateUserPackageImportButton();
      }
    }

    function bind() {
      if (bound) return false;
      bound = true;
      qa("[data-package-workflow]").forEach(button => button.addEventListener("click", () => selectPackageWorkflow(button.dataset.packageWorkflow, {focus: true})));
      selectPackageWorkflow("official");
      q("#fusion-package-official-select")?.addEventListener("click", () => void importOfficialPackage());
      q("#fusion-package-open-official-search")?.addEventListener("click", openOfficialSearch);
      q("#fusion-package-installed")?.addEventListener("click", event => {
        const button = event.target.closest?.("[data-package-rollback]");
        if (button) void rollbackOfficialPackage(button);
      });
      q("#fusion-package-literature-plan")?.addEventListener("submit", event => void planLiteraturePackage(event));
      q("#fusion-package-literature-export")?.addEventListener("click", () => void exportPlannedPackage("literature_collection"));
      q("#fusion-package-personal-plan")?.addEventListener("click", () => void planPersonalPackage());
      q("#fusion-package-personal-export")?.addEventListener("click", () => void exportPlannedPackage("personal_experiments"));
      q("#fusion-dataset-plan")?.addEventListener("submit", event => void planDataset(event));
      q("#fusion-dataset-include-private")?.addEventListener("change", resetDatasetPlanForScope);
      for (const selector of ["#fusion-dataset-rights-ack", "#fusion-dataset-unreviewed-ack"]) q(selector)?.addEventListener("change", updateDatasetExportButton);
      q("#fusion-dataset-export")?.addEventListener("click", () => void exportDataset());
      q("#fusion-package-user-select")?.addEventListener("click", () => void inspectUserPackage());
      q("#fusion-package-user-sha")?.addEventListener("input", updateUserPackageImportButton);
      for (const selector of ["#fusion-package-checksum-ack", "#fusion-package-unencrypted-ack", "#fusion-package-source-ack"]) q(selector)?.addEventListener("change", updateUserPackageImportButton);
      q("#fusion-package-user-import")?.addEventListener("click", () => void importUserPackage());
      q("#fusion-package-receipts-clear")?.addEventListener("click", () => void clearActivityReceipts());
      operationHistory?.bind();
      return true;
    }

    return Object.freeze({
      bind,
      selectPackageWorkflow,
      publicOfficialPackageResult,
      publicActiveOfficialPackageSummary,
      renderOfficialPackageResult,
      rememberOfficialPackageResult,
      publicDatasetPlan,
      publicDatasetReceipt,
      publicActivityReceiptSnapshot,
      loadActivityReceipts,
      deleteActivityReceipt,
      clearActivityReceipts,
      operationHistory,
      retryPackageReceipt,
      resumePackageJob,
      updateDatasetExportButton,
      planDataset,
      exportDataset,
      loadPackageCenter,
      importOfficialPackage,
      planLiteraturePackage,
      planPersonalPackage,
      exportPlannedPackage,
      inspectUserPackage,
      importUserPackage,
    });
  }

  globalThis.AutoResearchFusionPackage = Object.freeze({createPackageCenterController});
})();
