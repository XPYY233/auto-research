(() => {
  "use strict";

  const IMPORT_ID = /^personal_import_[A-Za-z0-9_-]{16,96}$/;
  const NEXT_ACTION_KEYS = Object.freeze([
    "entity_type", "entity_uid", "kind", "label", "source_id", "source_scope",
  ]);
  const PUBLIC_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,499}$/;

  function createPersonalImportController(ports) {
    if (!ports || typeof ports !== "object") throw new TypeError("personal_import_ports_required");
    const {
      state,
      q,
      qa,
      esc,
      cleanText,
      safeError,
      request,
      native,
      authorizeAI,
      executeAI,
      aiProgress,
      aiErrorCopy,
      resetAI,
      isPersonalActive,
      setOperation,
      selectCell,
      projectInspector,
      openImportedTable,
      confirmAction,
    } = ports;
    if (!state || typeof state !== "object") throw new TypeError("personal_import_state_required");
    for (const fn of [q, qa, esc, cleanText, safeError, request, authorizeAI, executeAI, aiProgress, aiErrorCopy, resetAI, isPersonalActive, setOperation, selectCell, projectInspector, openImportedTable, confirmAction]) {
      if (typeof fn !== "function") throw new TypeError("personal_import_port_invalid");
    }
    if (!native || typeof native.selectPersonalFile !== "function") throw new TypeError("personal_import_native_port_invalid");
    let bound = false;

    function personalStatus(message, kind = "info") {
      const node = q("#fusion-personal-status");
      if (node) {
        node.textContent = message;
        node.dataset.kind = kind;
      }
      setOperation(message, kind);
    }

    function previewSheet() {
      return state.personalPreview?.sheets?.[state.personalSheetIndex] || null;
    }

    function personalColumnRoleOptions(selected = "ignore") {
      const roles = ["independent", "dependent", "uncertainty", "condition", "identifier", "note", "ignore"];
      if (!roles.includes(selected)) selected = "ignore";
      const labels = {independent: "自变量", dependent: "因变量", uncertainty: "不确定度", condition: "实验条件", identifier: "标识符", note: "备注", ignore: "忽略"};
      return roles.map(value => `<option value="${value}"${value === selected ? " selected" : ""}>${labels[value]}</option>`).join("");
    }

    function personalSeriesColumns() {
      return qa("[data-personal-column]")
        .filter(row => row.querySelector("[data-personal-role]")?.value !== "ignore")
        .map(row => String(row.dataset.sourceName || ""))
        .filter(Boolean);
    }

    function personalSeriesOptions(columns, selected = "", optional = false) {
      const values = ["", ...columns];
      return values.map((value, index) => `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(index === 0 ? (optional ? "不使用不确定度列" : "请选择真实列") : value)}</option>`).join("");
    }

    function readPersonalSeriesEditor({strict = false} = {}) {
      const allowed = new Set(personalSeriesColumns());
      const seen = new Set();
      return qa("[data-personal-series-row]").map((row, index) => {
        const name = String(row.querySelector("[data-series-name]")?.value || "").trim();
        const x = String(row.querySelector("[data-series-x]")?.value || "");
        const y = String(row.querySelector("[data-series-y]")?.value || "");
        const uncertainty = String(row.querySelector("[data-series-uncertainty]")?.value || "");
        const description = String(row.querySelector("[data-series-description]")?.value || "").trim();
        const rawSeriesId = String(row.dataset.seriesId || "").trim();
        const seriesId = rawSeriesId || `series-${index + 1}`;
        if (strict && (!rawSeriesId || rawSeriesId.length > 240)) throw safeError("personal_review_incomplete", `第 ${index + 1} 个测量序列的标识无效，请移除后重新添加。`);
        if (strict && seen.has(seriesId)) throw safeError("personal_review_incomplete", "测量序列标识重复，请移除重复行后重新添加。");
        seen.add(seriesId);
        if (strict && (!name || !x || !y)) throw safeError("personal_review_incomplete", `请完整填写第 ${index + 1} 个测量序列。`);
        if (strict && ![x, y, uncertainty].filter(Boolean).every(value => allowed.has(value))) throw safeError("personal_review_incomplete", "被忽略或不属于当前工作表的列不能用于测量序列。");
        return {series_id: seriesId, name, x_column: x, y_column: y, ...(uncertainty ? {uncertainty_column: uncertainty} : {}), ...(description ? {description} : {})};
      });
    }

    function renderPersonalSeries(series = readPersonalSeriesEditor(), {warning = ""} = {}) {
      const host = q("#fusion-personal-series-list");
      const notice = q("#fusion-personal-series-warning");
      const columns = personalSeriesColumns();
      if (!host) return false;
      if (notice) {
        notice.textContent = warning;
        notice.hidden = !warning;
      }
      host.innerHTML = series.length ? series.map((value, index) => `<article class="fusion-personal-series-row" data-personal-series-row="${index}" data-series-id="${esc(value.series_id || `series-${index + 1}`)}"><div class="fusion-personal-series-fields"><label>序列名称<input data-series-name required maxlength="500" value="${esc(value.name || "")}" placeholder="例如：硬度随剂量变化"></label><label>横轴<select data-series-x>${personalSeriesOptions(columns, value.x_column || "")}</select></label><label>纵轴<select data-series-y>${personalSeriesOptions(columns, value.y_column || "")}</select></label><label>不确定度列（可选）<select data-series-uncertainty>${personalSeriesOptions(columns, value.uncertainty_column || "", true)}</select></label><label class="fusion-personal-series-description">说明（可选）<input data-series-description maxlength="1000" value="${esc(value.description || "")}"></label></div><button type="button" data-personal-series-remove="${index}" aria-label="移除测量序列 ${index + 1}">移除</button></article>`).join("") : `<p class="fusion-personal-series-empty">尚未添加测量序列。AI 不是必需项；可直接点击“添加序列”。</p>`;
      host.querySelectorAll("input,select").forEach(control => control.addEventListener("input", () => {
        state.personalAction += 1;
        q("#fusion-personal-confirm").disabled = !state.personalPreviewPage?.rows?.length;
        personalStatus("测量序列已修改；请核验后一次确认导入。", "info");
      }));
      host.querySelectorAll("[data-personal-series-remove]").forEach(button => button.addEventListener("click", () => removePersonalSeries(Number(button.dataset.personalSeriesRemove))));
      return true;
    }

    function addPersonalSeries(initial = null) {
      const columns = personalSeriesColumns();
      if (columns.length < 2) {
        personalStatus("至少保留两列未忽略的真实列，才能添加测量序列。", "error");
        return false;
      }
      const series = readPersonalSeriesEditor();
      const existing = new Set(series.map(value => value.series_id));
      const value = initial && typeof initial === "object" ? initial : {};
      const requestedId = String(value.series_id || "").trim();
      let seriesId = requestedId && requestedId.length <= 240 && !existing.has(requestedId) ? requestedId : "";
      while (!seriesId || existing.has(seriesId)) {
        state.personalSeriesCounter += 1;
        seriesId = `series-${state.personalSeriesCounter}`;
      }
      series.push({series_id: seriesId, name: String(value.name || ""), x_column: columns.includes(value.x_column) ? value.x_column : "", y_column: columns.includes(value.y_column) ? value.y_column : "", ...(columns.includes(value.uncertainty_column) ? {uncertainty_column: value.uncertainty_column} : {}), ...(value.description ? {description: String(value.description)} : {})});
      renderPersonalSeries(series);
      q(`[data-personal-series-row="${series.length - 1}"] [data-series-name]`)?.focus?.({preventScroll: true});
      return true;
    }

    function removePersonalSeries(index) {
      const series = readPersonalSeriesEditor();
      if (!Number.isInteger(index) || index < 0 || index >= series.length) return false;
      series.splice(index, 1);
      renderPersonalSeries(series);
      personalStatus("测量序列已移除；其余核验内容未改变。", "info");
      return true;
    }

    function refreshPersonalSeriesColumns({warning = true} = {}) {
      const series = readPersonalSeriesEditor();
      const allowed = new Set(personalSeriesColumns());
      const invalid = [];
      series.forEach((value, index) => {
        for (const field of ["x_column", "y_column", "uncertainty_column"]) {
          if (value[field] && !allowed.has(value[field])) {
            invalid.push(`${index + 1}:${value[field]}`);
            value[field] = "";
          }
        }
      });
      renderPersonalSeries(series, {warning: warning && invalid.length ? "列角色已改为忽略，相关序列引用已清空；请重新选择真实列。" : ""});
      return invalid.length;
    }

    function parsePersonalConditions(raw) {
      const output = {};
      for (const line of String(raw || "").split(/\r?\n/).map(value => value.trim()).filter(Boolean)) {
        const separator = line.indexOf("=");
        const key = separator >= 0 ? line.slice(0, separator).trim() : "";
        const value = separator >= 0 ? line.slice(separator + 1).trim() : "";
        if (!key || !value) throw safeError("personal_review_incomplete", "实验条件请按“名称=值”逐行填写，名称和值都不能为空。");
        if (Object.prototype.hasOwnProperty.call(output, key)) throw safeError("personal_review_incomplete", `实验条件“${key}”重复，请合并后再提交。`);
        output[key] = value;
      }
      return output;
    }

    function resetPersonalSheetEditors() {
      state.personalSeriesCounter = 0;
      q("#fusion-run-conditions").value = "";
      q("#fusion-run-note").value = "";
      renderPersonalSeries([], {warning: "已切换工作表；原工作表的测量序列、条件和备注已清空。"});
    }

    function renderPersonalColumns(columns) {
      const host = q("#fusion-personal-columns");
      if (!host) return false;
      host.innerHTML = columns.map((column, index) => `<article class="fusion-personal-column" data-personal-column="${index}" data-source-name="${esc(column.source_name)}"><strong>${esc(column.source_name)}</strong><label>角色<select data-personal-role>${personalColumnRoleOptions(column.role || "ignore")}</select></label><label>物理意义<input data-personal-meaning maxlength="500" value="${esc(column.meaning || column.source_name || "")}"></label><label>单位<input data-personal-unit maxlength="80" value="${esc(column.unit || "")}"></label><small data-personal-ai-note>${column.rationale ? esc(column.rationale) : "请逐列核验；不会自动确认。"}</small></article>`).join("");
      host.querySelectorAll("input,select").forEach(control => control.addEventListener("input", () => {
        state.personalAction += 1;
        q("#fusion-personal-confirm").disabled = !state.personalPreviewPage?.rows?.length;
        personalStatus("内容已修改；请完成集中核验后一次确认导入。", "info");
      }));
      host.querySelectorAll("[data-personal-role]").forEach(control => control.addEventListener("change", () => refreshPersonalSeriesColumns()));
      return true;
    }

    function personalImportRowsURL(importId, sheetIndex, page) {
      if (!IMPORT_ID.test(String(importId)) || !Number.isInteger(sheetIndex) || sheetIndex < 0 || sheetIndex > 999 || !Number.isInteger(page) || page < 1) return null;
      return `/api/desktop/personal-imports/${encodeURIComponent(importId)}/sheets/${sheetIndex}/rows?page=${page}&page_size=50`;
    }

    function publicPersonalImportPage(raw, {importId, sheetIndex, sheet, page}) {
      const expectedColumns = Array.isArray(sheet?.columns) ? sheet.columns.map(column => column?.source_name) : [];
      const totalRows = Number(sheet?.row_count);
      const expectedCount = Math.max(0, Math.min(50, totalRows - (page - 1) * 50));
      if (raw?.schema_version !== "personal-tabular-page-v1" || raw.import_id !== importId || raw.sheet_index !== sheetIndex || raw.sheet_name !== sheet?.sheet_name || raw.page !== page || raw.page_size !== 50 || !Number.isSafeInteger(totalRows) || totalRows < 0 || raw.total_rows !== totalRows || raw.has_next !== page * 50 < totalRows || !Array.isArray(raw.columns) || raw.columns.length !== expectedColumns.length || raw.columns.some((value, index) => typeof value !== "string" || value !== expectedColumns[index]) || !Array.isArray(raw.rows) || raw.rows.length !== expectedCount || raw.rows.some(row => !Array.isArray(row) || row.length !== expectedColumns.length || row.some(value => typeof value !== "string"))) return null;
      return {importId, sheetIndex, sheetName: raw.sheet_name, page, pageSize: 50, totalRows, hasNext: raw.has_next, columns: [...raw.columns], rows: raw.rows.map(row => [...row])};
    }

    function syncPersonalPageControls(page = null, {stateName = "idle", message = ""} = {}) {
      const host = q("#fusion-personal-page-controls");
      const status = q("#fusion-personal-page-status");
      const previous = q("#fusion-personal-page-prev");
      const next = q("#fusion-personal-page-next");
      if (host) host.hidden = false;
      if (status) {
        status.dataset.state = stateName;
        status.textContent = message;
      }
      if (previous) previous.disabled = !page || page.page <= 1;
      if (next) next.disabled = !page || !page.hasNext;
    }

    function renderPersonalPageTerminal(stateName, message, columns = []) {
      const table = q("#fusion-data-grid");
      if (!table) return false;
      table.querySelector("thead").innerHTML = `<tr><th>#</th>${columns.map((column, index) => `<th scope="col" data-column="${index}">${esc(column.source_name)}<small>${esc(column.data_type || "未知类型")}</small></th>`).join("")}</tr>`;
      table.querySelector("tbody").innerHTML = `<tr><td class="fusion-grid-terminal" colspan="${Math.max(1, columns.length + 1)}">${esc(message)}</td></tr>`;
      table.setAttribute("role", "grid");
      table.setAttribute("aria-rowcount", "1");
      table.setAttribute("aria-colcount", String(columns.length + 1));
      q("#fusion-personal-grid-wrap").hidden = false;
      syncPersonalPageControls(null, {stateName, message});
      projectInspector({kind: "page-terminal", state: stateName, message});
      return true;
    }

    function renderPersonalImportPage(page) {
      const sheet = previewSheet();
      const metadata = Array.isArray(sheet?.columns) ? sheet.columns : [];
      const table = q("#fusion-data-grid");
      const offset = (page.page - 1) * page.pageSize;
      const start = page.rows.length ? offset + 1 : 0;
      const end = offset + page.rows.length;
      state.personalPreviewPage = page;
      q("#fusion-sheet-summary").textContent = `${page.sheetName} · ${page.totalRows} 行 · ${page.columns.length} 列 · 真实分页数据`;
      table.querySelector("thead").innerHTML = `<tr><th>#</th>${page.columns.map((name, index) => `<th scope="col" data-column="${index}">${esc(name)}<small>${esc(metadata[index]?.data_type || "未知类型")}</small></th>`).join("")}</tr>`;
      table.querySelector("tbody").innerHTML = page.rows.length ? page.rows.map((row, rowIndex) => `<tr><th scope="row">${offset + rowIndex + 1}</th>${row.map((value, columnIndex) => `<td tabindex="-1" role="gridcell" aria-selected="false" data-cell data-row="${rowIndex}" data-column="${columnIndex}">${esc(value)}</td>`).join("")}</tr>`).join("") : `<tr><td class="fusion-grid-terminal" colspan="${Math.max(1, page.columns.length + 1)}">当前工作表没有数据行。</td></tr>`;
      table.setAttribute("role", "grid");
      table.setAttribute("aria-rowcount", String(page.totalRows + 1));
      table.setAttribute("aria-colcount", String(page.columns.length + 1));
      q("#fusion-personal-grid-wrap").hidden = false;
      syncPersonalPageControls(page, {stateName: page.rows.length ? "ready" : "empty", message: page.rows.length ? `第 ${start}–${end} 行 / 共 ${page.totalRows} 行` : "当前工作表为空 · 共 0 行"});
      qa("#fusion-data-grid [data-cell]").forEach(cell => cell.addEventListener("click", () => selectCell(Number(cell.dataset.row), Number(cell.dataset.column))));
      if (page.rows.length) selectCell(0, 0, {focus: false});
      else projectInspector({kind: "page-empty", page});
      return true;
    }

    async function loadPersonalPreviewPage(sheetIndex = state.personalSheetIndex, page = 1, {focusSelector = ""} = {}) {
      const importId = String(state.personalStatus?.import_id || "");
      const sheet = state.personalPreview?.sheets?.[sheetIndex];
      const url = personalImportRowsURL(importId, sheetIndex, page);
      if (!sheet || !url) return false;
      const generation = ++state.personalPageRequest;
      state.personalPreviewPage = null;
      q("#fusion-personal-confirm").disabled = true;
      q("#fusion-personal-ai").disabled = true;
      renderPersonalPageTerminal("loading", `正在读取第 ${page} 页真实数据…`, sheet.columns || []);
      try {
        const projected = publicPersonalImportPage(await request(url), {importId, sheetIndex, sheet, page});
        if (generation !== state.personalPageRequest || !isPersonalActive() || state.personalStatus?.import_id !== importId || state.personalSheetIndex !== sheetIndex) return false;
        if (!projected) throw safeError("personal_page_invalid", "实验工作表分页响应无效。");
        renderPersonalImportPage(projected);
        const usable = projected.rows.length > 0;
        q("#fusion-personal-confirm").disabled = !usable;
        q("#fusion-personal-ai").disabled = !usable;
        if (focusSelector) q(focusSelector)?.focus?.({preventScroll: true});
        return true;
      } catch (_error) {
        if (generation !== state.personalPageRequest || !isPersonalActive() || state.personalStatus?.import_id !== importId || state.personalSheetIndex !== sheetIndex) return false;
        state.personalPreviewPage = null;
        renderPersonalPageTerminal("error", "真实表格暂时无法读取；未保存任何内容，可重试或重新选择工作表。", sheet.columns || []);
        q("#fusion-personal-confirm").disabled = true;
        q("#fusion-personal-ai").disabled = true;
        return false;
      }
    }

    function choosePersonalSheet(index) {
      const sheets = state.personalPreview?.sheets || [];
      if (!Number.isInteger(index) || index < 0 || index >= sheets.length) return false;
      const changed = index !== state.personalSheetIndex;
      state.personalAction += 1;
      state.personalPageRequest += 1;
      state.personalSheetIndex = index;
      state.personalPreviewPage = null;
      state.personalSuggestion = null;
      q("#fusion-personal-sheet").value = String(index);
      qa("[data-personal-sheet-index]").forEach(button => button.classList.toggle("active", Number(button.dataset.personalSheetIndex) === index));
      renderPersonalPreviewSheet();
      if (changed) resetPersonalSheetEditors();
      void loadPersonalPreviewPage(index, 1);
      return true;
    }

    function bindPersonalSheetButtons() {
      qa("[data-personal-sheet-index]").forEach(button => button.addEventListener("click", () => choosePersonalSheet(Number(button.dataset.personalSheetIndex))));
    }

    function renderPersonalPreviewSheet() {
      const sheet = previewSheet();
      if (!sheet) return false;
      const columns = Array.isArray(sheet.columns) ? sheet.columns : [];
      q("#fusion-sheet-summary").textContent = `${sheet.sheet_name} · ${Number(sheet.row_count || 0)} 行 · ${columns.length} 列 · 正在读取真实分页数据`;
      renderPersonalColumns(columns);
      renderPersonalPageTerminal("loading", "正在读取第 1 页真实数据…", columns);
      return true;
    }

    function renderPersonalPreviewResponse(response) {
      if (response?.schema_version !== "personal-import-preview-v1" || response.status?.schema_version !== "personal-import-status-v1" || !IMPORT_ID.test(String(response.status.import_id || "")) || !Array.isArray(response.preview?.sheets)) throw safeError("personal_preview_invalid", "实验数据检查结果无效。");
      state.personalPreview = response.preview;
      state.personalStatus = response.status;
      state.personalSuggestion = null;
      state.personalSheetIndex = 0;
      state.personalPreviewPage = null;
      state.personalPageRequest += 1;
      resetAI("personal_suggestion");
      const filename = String(response.preview.source_file?.original_name || "本机实验数据");
      q("#fusion-personal-filename").textContent = filename;
      q("#fusion-personal-review").hidden = false;
      q("#fusion-personal-empty").hidden = true;
      q("#fusion-personal-context-file").textContent = filename;
      q("#fusion-personal-context-kind").textContent = "真实文件";
      const select = q("#fusion-personal-sheet");
      select.innerHTML = response.preview.sheets.map((sheet, index) => `<option value="${index}">${esc(sheet.sheet_name)} · ${Number(sheet.row_count || 0)} 行</option>`).join("");
      q("#fusion-personal-sheet-list").innerHTML = response.preview.sheets.map((sheet, index) => `<button type="button" class="fusion-tree-row${index === 0 ? " active" : ""}" data-personal-sheet-index="${index}"><span>${esc(sheet.sheet_name)}</span><b>${Number(sheet.row_count || 0)}×${Number(sheet.columns?.length || 0)}</b></button>`).join("");
      bindPersonalSheetButtons();
      const stem = String(response.preview.source_file?.original_name || "个人实验数据").replace(/\.[^.]+$/, " ").trim();
      q("#fusion-project-name").value = stem || "个人实验数据";
      q("#fusion-sample-name").value = String(response.preview.sheets[0]?.sheet_name || "实验样品");
      q("#fusion-run-name").value = String(response.preview.sheets[0]?.sheet_name || "实验批次");
      q("#fusion-run-method").value = "未注明";
      q("#fusion-personal-ai").disabled = true;
      q("#fusion-personal-confirm").disabled = true;
      q("#fusion-reviewed-state").textContent = "待集中核验";
      q("#fusion-review-context-state").textContent = "待核验";
      q("#fusion-ai-context-state").textContent = "未运行";
      renderPersonalPreviewSheet();
      resetPersonalSheetEditors();
      void loadPersonalPreviewPage(0, 1);
      personalStatus("文件检查完成，正在读取所选工作表的真实分页数据。AI 预填可选；也可直接手工建立测量序列。", "success");
    }

    async function choosePersonalFile() {
      const generation = ++state.personalAction;
      personalStatus("正在打开系统文件选择器…", "loading");
      try {
        const selected = await native.selectPersonalFile();
        if (generation !== state.personalAction || !isPersonalActive()) return null;
        if (selected?.cancelled) {
          personalStatus("已取消选择，没有读取文件。", "info");
          return null;
        }
        if (selected?.ok !== true || typeof selected.selection?.selection_id !== "string") throw safeError("personal_picker_failed", "实验数据文件选择失败。");
        const response = await request("/api/desktop/personal-imports/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({selection_id: selected.selection.selection_id})});
        if (generation !== state.personalAction || !isPersonalActive()) return null;
        renderPersonalPreviewResponse(response);
        return response;
      } catch (_error) {
        if (generation === state.personalAction) personalStatus("实验数据检查未完成；未保存任何内容。", "error");
        return null;
      }
    }

    function applyPersonalSuggestion(suggestion) {
      if (suggestion?.schema_version !== "personal-import-suggestion-v1" || suggestion.import_id !== state.personalStatus?.import_id) return false;
      state.personalSuggestion = suggestion;
      aiProgress("personal_suggestion", "applying", "running", "正在将建议写入待核验表单");
      const values = {"#fusion-project-name": suggestion.project?.name, "#fusion-sample-name": suggestion.sample?.name, "#fusion-sample-material": suggestion.sample?.material, "#fusion-run-name": suggestion.run?.name, "#fusion-run-method": suggestion.run?.method};
      for (const [selector, value] of Object.entries(values)) if (typeof value === "string" && value.trim()) q(selector).value = value;
      const byName = new Map((Array.isArray(suggestion.columns) ? suggestion.columns : []).map(column => [column.source_name, column]));
      qa("[data-personal-column]").forEach(row => {
        const column = byName.get(row.dataset.sourceName);
        if (!column) return;
        row.querySelector("[data-personal-role]").value = column.role || "ignore";
        row.querySelector("[data-personal-meaning]").value = column.meaning || "";
        row.querySelector("[data-personal-unit]").value = column.unit || "";
        row.querySelector("[data-personal-ai-note]").textContent = String(column.rationale || "AI 建议，请人工核验。");
      });
      const conditions = suggestion.run?.conditions && typeof suggestion.run.conditions === "object" && !Array.isArray(suggestion.run.conditions) ? suggestion.run.conditions : {};
      q("#fusion-run-conditions").value = Object.entries(conditions).map(([key, value]) => `${key}=${value}`).join("\n");
      q("#fusion-run-note").value = typeof suggestion.run?.user_note === "string" ? suggestion.run.user_note : "";
      const suggestedSeries = Array.isArray(suggestion.series) ? suggestion.series.map((value, index) => ({series_id: String(value.series_id || `series-${index + 1}`), name: String(value.name || ""), x_column: String(value.x_column || ""), y_column: String(value.y_column || ""), ...(value.uncertainty_column ? {uncertainty_column: String(value.uncertainty_column)} : {}), ...(value.description ? {description: String(value.description)} : {})})) : [];
      state.personalSeriesCounter = Math.max(state.personalSeriesCounter, suggestedSeries.length);
      renderPersonalSeries(suggestedSeries);
      refreshPersonalSeriesColumns();
      q("#fusion-personal-confirm").disabled = !state.personalPreviewPage?.rows?.length;
      q("#fusion-ai-context-state").textContent = "待人工核验";
      personalStatus("AI 预填完成；列、序列、条件和备注都可继续修改或删除。", "success");
      aiProgress("personal_suggestion", "completed", "success", "AI 建议已写入可编辑表单，等待人工核验");
      return true;
    }

    async function requestPersonalSuggestion() {
      if (!state.personalStatus?.import_id || !state.personalPreviewPage?.rows?.length) return;
      const generation = ++state.personalAction;
      q("#fusion-personal-ai").disabled = true;
      q("#fusion-personal-confirm").disabled = true;
      personalStatus("正在准备 AI 辅助预填…", "loading");
      aiProgress("personal_suggestion", "prepare", "running", "正在整理有界表头与样例");
      try {
        aiProgress("personal_suggestion", "authorization", "waiting", "等待你确认发送范围与预算");
        const authorization = await authorizeAI({import_id: state.personalStatus.import_id, sheet_index: state.personalSheetIndex});
        if (generation !== state.personalAction || !isPersonalActive()) return;
        if (!authorization) {
          personalStatus("已取消；没有向 AI 提供商发送工作表内容。", "info");
          aiProgress("personal_suggestion", "authorization", "cancelled", "已取消；没有发送工作表内容");
          return;
        }
        aiProgress("personal_suggestion", "analyzing", "running", "正在分析列角色、物理意义与单位");
        const suggestion = await executeAI(authorization);
        if (generation !== state.personalAction || !isPersonalActive()) return;
        applyPersonalSuggestion(suggestion);
      } catch (error) {
        if (generation === state.personalAction) {
          const message = aiErrorCopy(error, "AI 预填未完成；本地文件检查结果仍可人工核验。");
          personalStatus(message, "error");
          aiProgress("personal_suggestion", "analyzing", "error", "AI 预填未完成", message);
        }
      } finally {
        if (generation === state.personalAction) {
          const usable = Boolean(state.personalPreviewPage?.rows?.length);
          q("#fusion-personal-ai").disabled = !usable;
          q("#fusion-personal-confirm").disabled = !usable;
        }
      }
    }

    function collectPersonalDraft() {
      const sheet = previewSheet();
      const columns = qa("[data-personal-column]").map(row => {
        const role = row.querySelector("[data-personal-role]").value;
        const meaning = row.querySelector("[data-personal-meaning]").value.trim();
        const unit = row.querySelector("[data-personal-unit]").value.trim();
        if (role !== "ignore" && !meaning) throw safeError("personal_review_incomplete", `请填写“${row.dataset.sourceName}”的物理意义。`);
        return {source_name: row.dataset.sourceName, role, meaning: meaning || null, unit: unit || null};
      });
      const project = q("#fusion-project-name").value.trim();
      const sample = q("#fusion-sample-name").value.trim();
      const run = q("#fusion-run-name").value.trim();
      const method = q("#fusion-run-method").value.trim();
      if (!sheet || !project || !sample || !run || !method) throw safeError("personal_review_incomplete", "请填写完整的项目、样品和实验批次信息。");
      const series = readPersonalSeriesEditor({strict: true});
      const conditions = parsePersonalConditions(q("#fusion-run-conditions").value);
      const userNote = q("#fusion-run-note").value.trim();
      const draft = {sheet_index: state.personalSheetIndex, project: {name: project}, sample: {name: sample}, run: {name: run, method, conditions}, columns, series};
      const material = q("#fusion-sample-material").value.trim();
      if (material) draft.sample.material = material;
      if (userNote) draft.run.user_note = userNote;
      if (Number.isInteger(state.personalStatus?.revision)) draft.expected_revision = state.personalStatus.revision;
      return draft;
    }

    function publicPersonalImportNextAction(raw) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw) || Object.keys(raw).sort().join("|") !== NEXT_ACTION_KEYS.join("|") || raw.kind !== "open_personal_table" || raw.source_scope !== "private" || raw.entity_type !== "table" || raw.label !== "打开刚导入的表格" || typeof raw.source_id !== "string" || typeof raw.entity_uid !== "string" || !PUBLIC_ID.test(raw.source_id) || !PUBLIC_ID.test(raw.entity_uid)) return null;
      return Object.freeze({sourceScope: "private", sourceId: raw.source_id, entityType: "table", entityUid: raw.entity_uid, label: "打开刚导入的表格"});
    }

    function markPersonalImportSavedPending(message) {
      q("#fusion-reviewed-state").textContent = "✓ 已核验并保存";
      q("#fusion-review-context-state").textContent = "已保存，待恢复";
      q("#fusion-personal-confirm").disabled = true;
      personalStatus(message, "warning");
    }

    async function confirmPersonalImport() {
      const importId = String(state.personalStatus?.import_id || "");
      const page = state.personalPreviewPage;
      if (!IMPORT_ID.test(importId)) return;
      if (!page || page.importId !== importId || page.sheetIndex !== state.personalSheetIndex || !page.rows.length) {
        personalStatus("请先成功读取当前工作表的真实分页数据，再确认导入。", "error");
        return;
      }
      let draft;
      try {
        draft = collectPersonalDraft();
      } catch (error) {
        personalStatus(error.message, "error");
        return;
      }
      const sheetName = cleanText(previewSheet()?.sheet_name || "当前工作表", 200);
      if (!confirmAction(`确认只将工作表“${sheetName}”的当前核验结果保存到本机私人实验库？\n\n其他工作表不会同时写入；这是一次本地写入，不会发送给 AI。`)) return;
      const generation = ++state.personalAction;
      q("#fusion-personal-confirm").disabled = true;
      personalStatus("正在保存核验结果并准备打开表格…", "loading");
      let saved = false;
      try {
        const result = await request(`/api/desktop/personal-imports/${encodeURIComponent(importId)}/reviewed-import`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reviewed: true, draft})});
        if (generation !== state.personalAction) return;
        if (result?.schema_version !== "personal-import-status-v1" || result.indexable !== true) throw safeError("personal_import_incomplete", "实验数据尚未进入可检索状态。");
        saved = true;
        state.personalStatus = result;
        q("#fusion-reviewed-state").textContent = "✓ 已核验并导入";
        q("#fusion-review-context-state").textContent = "已导入";
        if (!publicPersonalImportNextAction(result.next_action)) {
          markPersonalImportSavedPending("数据已保存，但表格暂时无法打开；可以从“我的实验”搜索中查找。");
          return;
        }
        personalStatus("实验数据已保存，正在打开刚导入的表格…", "success");
        const opened = await openImportedTable(result.next_action, generation);
        if (generation === state.personalAction && isPersonalActive()) {
          if (opened) personalStatus("实验数据已保存，刚导入的表格已打开。", "success");
          else markPersonalImportSavedPending("数据已保存，但表格暂时无法打开；可以从“我的实验”搜索中查找。");
        }
      } catch (error) {
        if (generation === state.personalAction) {
          const savedCode = cleanText(error?.code, 80);
          if (saved || ["personal_next_action_unavailable", "personal_search_refresh_failed"].includes(savedCode)) {
            const message = savedCode === "personal_search_refresh_failed" ? "数据已保存，但搜索索引待恢复；可以稍后从“我的实验”搜索中查看。" : "数据已保存，但表格暂时无法打开；可以从“我的实验”搜索中查找。";
            markPersonalImportSavedPending(message);
          } else {
            personalStatus("导入未完成；不会显示假成功，请核对后重试。", "error");
            q("#fusion-personal-confirm").disabled = !state.personalPreviewPage?.rows?.length;
          }
        }
      }
    }

    function bind() {
      if (bound) return false;
      bound = true;
      q("#fusion-select-data-file")?.addEventListener("click", () => void choosePersonalFile());
      q("#fusion-select-data-file-empty")?.addEventListener("click", () => void choosePersonalFile());
      q("#fusion-personal-ai")?.addEventListener("click", () => void requestPersonalSuggestion());
      q("#fusion-personal-confirm")?.addEventListener("click", () => void confirmPersonalImport());
      q("#fusion-personal-series-add")?.addEventListener("click", () => addPersonalSeries());
      for (const selector of ["#fusion-run-conditions", "#fusion-run-note"]) q(selector)?.addEventListener("input", () => {
        state.personalAction += 1;
        q("#fusion-personal-confirm").disabled = !state.personalPreviewPage?.rows?.length;
        personalStatus("实验条件或备注已修改；请核验后一次确认导入。", "info");
      });
      q("#fusion-personal-sheet")?.addEventListener("change", event => choosePersonalSheet(Number(event.currentTarget.value) || 0));
      q("#fusion-personal-page-prev")?.addEventListener("click", () => {
        const page = state.personalPreviewPage;
        if (page?.page > 1) void loadPersonalPreviewPage(page.sheetIndex, page.page - 1, {focusSelector: "#fusion-personal-page-prev"});
      });
      q("#fusion-personal-page-next")?.addEventListener("click", () => {
        const page = state.personalPreviewPage;
        if (page?.hasNext) void loadPersonalPreviewPage(page.sheetIndex, page.page + 1, {focusSelector: "#fusion-personal-page-next"});
      });
      return true;
    }

    return Object.freeze({
      bind,
      previewSheet,
      publicPersonalImportPage,
      loadPersonalPreviewPage,
      choosePersonalSheet,
      choosePersonalFile,
      requestPersonalSuggestion,
      applyPersonalSuggestion,
      addPersonalSeries,
      removePersonalSeries,
      refreshPersonalSeriesColumns,
      parsePersonalConditions,
      collectPersonalDraft,
      publicPersonalImportNextAction,
      confirmPersonalImport,
    });
  }

  globalThis.AutoResearchFusionPersonalImport = Object.freeze({createPersonalImportController});
})();
