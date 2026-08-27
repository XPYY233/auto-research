(() => {
  "use strict";

  const ROOT_KEYS = Object.freeze(["operations", "revision", "schema_version", "storage"]);
  const ENTRY_KEYS = Object.freeze(["created_at", "error", "expires_at", "next_action", "operation", "operation_uid", "outcome", "progress", "receipt_status", "schema_version", "stage", "state", "terminal", "updated_at"]);
  const ERROR_KEYS = Object.freeze(["code", "message", "retryable", "stage"]);
  const STATES = new Set(["queued", "running", "completed", "failed", "interrupted"]);
  const OPERATIONS = new Set(["transfer_import", "transfer_export", "dataset_export"]);
  const RECEIPTS = new Set([null, "pending", "stored"]);
  const UID = /^[0-9a-f]{64}$/;
  const SAFE_NAME = /^[a-z][a-z0-9_.-]{0,79}$/;
  const STORAGE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
  const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
  const STATE_LABELS = Object.freeze({queued: "等待开始", running: "正在进行", completed: "已完成", failed: "未完成", interrupted: "已中断"});
  const OPERATION_LABELS = Object.freeze({transfer_import: "用户资料包导入", transfer_export: "用户资料包导出", dataset_export: "训练数据集导出"});
  const STAGE_LABELS = Object.freeze({queued: "等待开始", plan: "核对范围", rights_audit: "核对权限", build_archive: "生成资料包", audit_payload: "检查内容", publish: "写入所选位置", completed: "任务完成", failed: "任务未完成"});

  function createOperationHistoryController(ports) {
    if (!ports || typeof ports !== "object") throw new TypeError("operation_history_ports_required");
    const {q, qa, request, safeError, cleanText, esc, onCountChange, onReturnToWorkflow, onReceiptStored} = ports;
    for (const fn of [q, qa, request, safeError, cleanText, esc, onCountChange, onReturnToWorkflow, onReceiptStored]) if (typeof fn !== "function") throw new TypeError("operation_history_port_invalid");
    const state = {revision: null, operations: [], status: "idle", notice: null, request: 0, bound: false, busy: new Set()};

    const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
    const safeInteger = value => !Number.isNaN(value) && Number.isSafeInteger(value) && value >= 0;
    const pathFree = value => {
      const encoded = JSON.stringify(value);
      return !/(?:file:\/\/|\/[A-Za-z0-9_. -]+\/|[A-Za-z]:\\|(?:sk|ds)-[A-Za-z0-9_-]{8,})/i.test(encoded);
    };
    function publicError(value) {
      if (value === null) return null;
      if (!exactKeys(value, ERROR_KEYS) || !SAFE_NAME.test(String(value.code || "")) || !SAFE_NAME.test(String(value.stage || "")) || typeof value.message !== "string" || !value.message || value.message.length > 500 || typeof value.retryable !== "boolean" || !pathFree(value)) return null;
      return Object.freeze({code: value.code, message: cleanText(value.message, 500), stage: value.stage, retryable: value.retryable});
    }
    function publicOperation(value) {
      if (!exactKeys(value, ENTRY_KEYS) || value.schema_version !== "operation-history-entry-v1" || !UID.test(String(value.operation_uid || "")) || !OPERATIONS.has(value.operation) || !STATES.has(value.state) || !SAFE_NAME.test(String(value.stage || "")) || !safeInteger(value.progress) || value.progress > 100 || typeof value.terminal !== "boolean" || !RECEIPTS.has(value.receipt_status) || !TIMESTAMP.test(String(value.created_at || "")) || !TIMESTAMP.test(String(value.updated_at || "")) || !TIMESTAMP.test(String(value.expires_at || "")) || !["none", "restart_operation", "retry_receipt"].includes(value.next_action) || !pathFree(value)) return null;
      const error = publicError(value.error);
      if (value.error !== null && !error) return null;
      const timestamps = [value.created_at, value.updated_at, value.expires_at].map(Date.parse);
      if (timestamps.some(timestamp => !Number.isFinite(timestamp)) || timestamps[0] > timestamps[1] || timestamps[1] >= timestamps[2]) return null;
      const noTerminalPayload = value.error === null && value.outcome === null && value.receipt_status === null;
      const semantic = value.state === "queued"
        ? value.terminal === false && value.progress === 0 && value.next_action === "none" && noTerminalPayload
        : value.state === "running"
          ? value.terminal === false && value.progress < 100 && value.next_action === "none" && noTerminalPayload
          : value.state === "interrupted"
            ? value.terminal === true && value.next_action === "restart_operation" && noTerminalPayload
            : value.state === "failed"
              ? value.terminal === true && value.next_action === (error?.retryable ? "restart_operation" : "none") && Boolean(error) && value.outcome === null && value.receipt_status === null
              : value.terminal === true && value.progress === 100 && value.error === null && typeof value.outcome === "string" && value.outcome.length > 0 && (value.receipt_status === "pending" ? value.next_action === "retry_receipt" : value.next_action === "none");
      if (!semantic || value.operation === "transfer_import" && value.receipt_status !== null || value.receipt_status === "pending" && !["transfer_export", "dataset_export"].includes(value.operation)) return null;
      return Object.freeze({uid: value.operation_uid, operation: value.operation, state: value.state, stage: value.stage, progress: value.progress, outcome: typeof value.outcome === "string" ? cleanText(value.outcome, 80) : null, receiptStatus: value.receipt_status, createdAt: value.created_at, updatedAt: value.updated_at, expiresAt: value.expires_at, nextAction: value.next_action, error});
    }
    function publicSnapshot(value) {
      if (!exactKeys(value, ROOT_KEYS) || value.schema_version !== "operation-history-v1" || !safeInteger(value.revision) || !STORAGE.test(String(value.storage || "")) || !Array.isArray(value.operations) || value.operations.length > 100 || !pathFree(value)) return null;
      const operations = value.operations.map(publicOperation);
      return operations.every(Boolean) ? Object.freeze({revision: value.revision, operations: Object.freeze(operations)}) : null;
    }
    function timeLabel(value) {
      try { return new Intl.DateTimeFormat("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(value)); } catch (_error) { return "时间不可用"; }
    }
    function render() {
      const host = q("#fusion-package-history"), status = q("#fusion-package-history-status"), clear = q("#fusion-package-history-clear");
      onCountChange(state.operations.length);
      if (!host || !status || !clear) return;
      clear.disabled = state.status === "loading" || !state.operations.length;
      if (state.status === "loading") { status.textContent = "正在读取最近任务…"; status.dataset.kind = "loading"; host.innerHTML = "<p>读取完成后将在此显示。</p>"; return; }
      if (state.status === "error") { status.textContent = "最近任务暂时不可用；当前任务与已导出文件不受影响。"; status.dataset.kind = "error"; host.innerHTML = "<p>稍后可重新进入资料包中心读取。</p>"; return; }
      status.textContent = state.notice?.message || (state.operations.length ? `近 30 天共有 ${state.operations.length} 条任务记录。` : "近 30 天没有任务记录。");
      status.dataset.kind = state.notice?.kind || (state.operations.length ? "success" : "empty");
      host.innerHTML = state.operations.length ? state.operations.map((item, index) => {
        const pending = item.state === "completed" && item.receiptStatus === "pending";
        const detail = pending ? "文件已导出，回执待恢复，请勿重复导出" : item.error ? item.error.message : `${STAGE_LABELS[item.stage] || item.stage} · ${item.progress}%`;
        const action = pending ? `<button type="button" data-operation-history-retry="${index}"${state.busy.has(item.uid) ? " disabled" : ""}>${state.busy.has(item.uid) ? "正在恢复…" : "恢复完成回执"}</button>` : item.state === "interrupted" ? `<button type="button" data-operation-history-return="${index}">返回相应流程</button>` : "";
        return `<article class="fusion-operation-history-row" data-state="${esc(item.state)}"><div><strong>${esc(OPERATION_LABELS[item.operation])}</strong><small>${esc(timeLabel(item.updatedAt))} · ${esc(STATE_LABELS[item.state])}</small><p>${esc(detail)}</p></div><div class="fusion-operation-history-actions">${action}<button type="button" data-operation-history-delete="${index}"${state.busy.has(item.uid) ? " disabled" : ""}>删除记录</button></div></article>`;
      }).join("") : "<p>任务开始后会在这里保留安全状态，不显示路径、结果内容或内部任务编号。</p>";
      qa("[data-operation-history-retry]").forEach(button => button.addEventListener("click", () => void retryReceipt(Number(button.dataset.operationHistoryRetry))));
      qa("[data-operation-history-return]").forEach(button => button.addEventListener("click", () => returnToWorkflow(Number(button.dataset.operationHistoryReturn))));
      qa("[data-operation-history-delete]").forEach(button => button.addEventListener("click", () => void deleteOperation(Number(button.dataset.operationHistoryDelete))));
    }
    function applySnapshot(snapshot) { state.revision = snapshot.revision; state.operations = [...snapshot.operations]; state.status = "ready"; state.notice = null; render(); return state.operations; }
    async function load({force = false} = {}) {
      if (state.status === "loading" && !force) return state.operations;
      const generation = ++state.request; state.status = "loading"; state.notice = null; render();
      try { const snapshot = publicSnapshot(await request("/api/desktop/package-center/history")); if (!snapshot) throw safeError("operation_history_invalid", "资料包任务历史格式无效。"); if (generation !== state.request) return state.operations; return applySnapshot(snapshot); }
      catch (_error) { if (generation === state.request) { state.status = "error"; render(); } return state.operations; }
    }
    async function mutate(body) {
      try { const snapshot = publicSnapshot(await request("/api/desktop/package-center/history", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})); if (!snapshot) throw safeError("operation_history_invalid", "资料包任务历史格式无效。"); applySnapshot(snapshot); return true; }
      catch (error) { if (error?.code === "operation_history_revision_conflict") { await load({force: true}); state.notice = {message: "任务历史已更新，现已刷新；请确认后重试。", kind: "error"}; render(); } else { state.status = "error"; render(); } return false; }
    }
    async function deleteOperation(index) { const item = state.operations[index]; if (!item || !Number.isSafeInteger(state.revision) || state.busy.has(item.uid)) return false; state.busy.add(item.uid); render(); try { return await mutate({operation: "delete", expected_revision: state.revision, operation_uid: item.uid}); } finally { state.busy.delete(item.uid); render(); } }
    async function clear() { if (!state.operations.length || !Number.isSafeInteger(state.revision) || typeof globalThis.confirm !== "function" || !globalThis.confirm("确认清空近 30 天资料包任务历史？这不会删除已导出的文件或最近完成回执。")) return false; return mutate({operation: "clear", expected_revision: state.revision, confirm_clear: true}); }
    async function retryReceipt(index) {
      const item = state.operations[index];
      if (!item || item.nextAction !== "retry_receipt" || item.receiptStatus !== "pending" || !Number.isSafeInteger(state.revision) || state.busy.has(item.uid)) return false;
      state.busy.add(item.uid); render();
      try {
        const snapshot = publicSnapshot(await request(`/api/desktop/package-center/history/${item.uid}/receipt-retry`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({expected_revision: state.revision})}));
        if (!snapshot) throw safeError("operation_history_invalid", "完成回执恢复结果无效。");
        applySnapshot(snapshot); await onReceiptStored(); return true;
      } catch (error) {
        if (error?.code === "operation_history_revision_conflict") { await load({force: true}); state.notice = {message: "任务历史已更新，现已刷新；请确认后重试。", kind: "error"}; }
        else state.notice = {message: "文件已导出，请勿重复导出；完成回执暂未恢复，可再次尝试。", kind: "error"};
        return false;
      } finally { state.busy.delete(item.uid); render(); }
    }
    function returnToWorkflow(index) { const item = state.operations[index]; if (!item || item.state !== "interrupted" || item.nextAction !== "restart_operation") return false; onReturnToWorkflow(item.operation); return true; }
    function bind() { if (state.bound) return false; state.bound = true; q("#fusion-package-history-clear")?.addEventListener("click", () => void clear()); return true; }
    return Object.freeze({bind, load, render, publicSnapshot, deleteOperation, clear, retryReceipt, returnToWorkflow, state});
  }

  globalThis.AutoResearchFusionOperationHistory = Object.freeze({createOperationHistoryController});
})();
